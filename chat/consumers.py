import logging
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import F
from django.utils import timezone

from .models import PRESENCE_STALE_SECONDS, GroupMember, Message, User

logger = logging.getLogger(__name__)
MAX_LEN = 2000


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """One socket per browser tab: private + group messages, seen, delete, typing, presence."""

    async def connect(self):
        user = self.scope.get("user")
        if (not user or not user.is_authenticated or not user.is_active or not user.company_id
                or user.must_change_password):
            await self.close(code=4401)
            return
        self.uid = user.id
        self.cid = user.company_id
        self.user_group = f"user_{self.uid}"
        self.company_group = f"company_{self.cid}"
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.channel_layer.group_add(self.company_group, self.channel_name)
        await self.accept()
        await self._presence_connect()
        await self.channel_layer.group_send(
            self.company_group, {"type": "presence.update", "user_id": self.uid, "online": True})

    async def disconnect(self, code):
        if not hasattr(self, "uid"):
            return
        still_online = await self._presence_disconnect()
        await self.channel_layer.group_send(
            self.company_group, {"type": "presence.update", "user_id": self.uid, "online": still_online})
        await self.channel_layer.group_discard(self.user_group, self.channel_name)
        await self.channel_layer.group_discard(self.company_group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        kind = content.get("type") if isinstance(content, dict) else None
        if kind == "ping":
            await self._touch()
            await self.send_json({"type": "pong"})
        elif kind == "message":
            await self._handle_message(content)
        elif kind == "group_message":
            await self._handle_group_message(content)
        elif kind == "typing":
            peer = await self._valid_peer(content.get("to"))
            if peer:
                await self.channel_layer.group_send(
                    f"user_{peer}", {"type": "chat.typing", "from_id": self.uid})
        elif kind == "read":
            peer = content.get("peer")
            if await self._mark_read(peer):
                await self._push([int(peer)], {"type": "seen", "by": self.uid})
        elif kind == "read_group":
            await self._mark_group_read(content.get("group"))
        elif kind == "delete":
            await self._handle_delete(content.get("id"))

    # ---- sending ----
    @staticmethod
    def _clean(content):
        body = str(content.get("body", "")).strip()
        return body if body and len(body) <= MAX_LEN else None

    async def _handle_message(self, content):
        body = self._clean(content)
        if body is None:
            await self.send_json({"type": "error", "error": "Message must be 1-2000 characters."})
            return
        payload = await self._save(content.get("to"), body)
        if payload is None:
            await self.send_json({"type": "error", "error": "You cannot message that person."})
            return
        payload["client_id"] = str(content.get("client_id", ""))[:40]
        for group in {self.user_group, f"user_{payload['recipient']}"}:
            await self.channel_layer.group_send(group, {"type": "chat.message", "payload": payload})

    async def _handle_group_message(self, content):
        body = self._clean(content)
        if body is None:
            await self.send_json({"type": "error", "error": "Message must be 1-2000 characters."})
            return
        res = await self._save_group(content.get("group"), body)
        if res is None:
            await self.send_json({"type": "error", "error": "You are not a member of that group."})
            return
        payload, member_ids = res
        for uid in member_ids:
            await self.channel_layer.group_send(f"user_{uid}", {"type": "chat.message", "payload": payload})

    async def _handle_delete(self, mid):
        res = await self._delete(mid)
        if res is None:
            await self.send_json({"type": "error", "error": "You can only delete your own messages."})
            return
        payload, ids = res
        await self._push(ids, payload)

    async def _push(self, user_ids, payload):
        for uid in set(user_ids):
            await self.channel_layer.group_send(f"user_{uid}", {"type": "chat.event", "payload": payload})

    # ---- group event handlers (server -> this socket) ----
    async def chat_message(self, event):
        await self.send_json(event["payload"])

    async def chat_event(self, event):
        await self.send_json(event["payload"])

    async def chat_typing(self, event):
        await self.send_json({"type": "typing", "from": event["from_id"]})

    async def presence_update(self, event):
        await self.send_json({"type": "presence", "user_id": event["user_id"], "online": event["online"]})

    # ---- database helpers ----
    @database_sync_to_async
    def _presence_connect(self):
        now = timezone.now()
        # Reset a counter left behind by a crashed/restarted server.
        User.objects.filter(pk=self.uid, last_seen__lt=now - timedelta(seconds=PRESENCE_STALE_SECONDS)
                            ).update(connections=0)
        User.objects.filter(pk=self.uid).update(connections=F("connections") + 1, last_seen=now)

    @database_sync_to_async
    def _presence_disconnect(self):
        User.objects.filter(pk=self.uid, connections__gt=0).update(
            connections=F("connections") - 1, last_seen=timezone.now())
        return User.objects.filter(pk=self.uid, connections__gt=0).exists()

    @database_sync_to_async
    def _touch(self):
        User.objects.filter(pk=self.uid).update(last_seen=timezone.now())

    @staticmethod
    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @database_sync_to_async
    def _valid_peer(self, peer_id):
        """Return peer id only if it is another ACTIVE user of the SAME company (isolation)."""
        peer_id = self._int(peer_id)
        if peer_id is None:
            return None
        ok = User.objects.filter(pk=peer_id, company_id=self.cid, is_active=True).exclude(pk=self.uid).exists()
        return peer_id if ok else None

    @database_sync_to_async
    def _save(self, peer_id, body):
        peer_id = self._int(peer_id)
        if peer_id is None:
            return None
        sender = User.objects.filter(pk=self.uid, is_active=True).first()   # may have been deactivated
        peer = User.objects.filter(pk=peer_id, company_id=self.cid, is_active=True).exclude(pk=self.uid).first()
        if not sender or not peer:
            return None
        msg = Message.objects.create(company_id=self.cid, sender=sender, recipient=peer, body=body)
        return msg.as_dict()

    @database_sync_to_async
    def _save_group(self, gid, body):
        gid = self._int(gid)
        if gid is None:
            return None
        sender = User.objects.filter(pk=self.uid, is_active=True).first()
        if not sender or not GroupMember.objects.filter(
                group_id=gid, user_id=self.uid, group__company_id=self.cid).exists():
            return None
        msg = Message.objects.create(company_id=self.cid, sender=sender, group_id=gid, body=body)
        GroupMember.objects.filter(group_id=gid, user_id=self.uid).update(last_read_at=msg.created_at)
        members = list(GroupMember.objects.filter(group_id=gid).values_list("user_id", flat=True))
        return msg.as_dict(), members

    @database_sync_to_async
    def _delete(self, mid):
        mid = self._int(mid)
        if mid is None:
            return None
        msg = Message.objects.filter(pk=mid, company_id=self.cid, sender_id=self.uid,
                                     deleted_at__isnull=True).first()
        if not msg:
            return None
        msg.body = ""
        msg.deleted_at = timezone.now()
        msg.save(update_fields=["body", "deleted_at"])
        if msg.recipient_id:
            ids = [self.uid, msg.recipient_id]
        else:
            ids = list(GroupMember.objects.filter(group_id=msg.group_id).values_list("user_id", flat=True))
        return ({"type": "deleted", "id": msg.id, "sender": self.uid, "recipient": msg.recipient_id,
                 "group": msg.group_id}, ids)

    @database_sync_to_async
    def _mark_read(self, peer_id):
        peer_id = self._int(peer_id)
        if peer_id is None:
            return 0
        return Message.objects.filter(company_id=self.cid, sender_id=peer_id, recipient_id=self.uid,
                                      read_at__isnull=True).update(read_at=timezone.now())

    @database_sync_to_async
    def _mark_group_read(self, gid):
        gid = self._int(gid)
        if gid is not None:
            GroupMember.objects.filter(group_id=gid, user_id=self.uid, group__company_id=self.cid
                                       ).update(last_read_at=timezone.now())
