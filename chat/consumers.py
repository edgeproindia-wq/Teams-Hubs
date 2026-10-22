import logging
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import F
from django.utils import timezone

from .models import PRESENCE_STALE_SECONDS, Message, User

logger = logging.getLogger(__name__)
MAX_LEN = 2000


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """One socket per browser tab. Handles private messages, typing and presence."""

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated or not user.is_active or not user.company_id:
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
        elif kind == "typing":
            peer = await self._valid_peer(content.get("to"))
            if peer:
                await self.channel_layer.group_send(
                    f"user_{peer}", {"type": "chat.typing", "from_id": self.uid})
        elif kind == "read":
            await self._mark_read(content.get("peer"))

    async def _handle_message(self, content):
        body = str(content.get("body", "")).strip()
        if not body or len(body) > MAX_LEN:
            await self.send_json({"type": "error", "error": "Message must be 1-2000 characters."})
            return
        msg = await self._save(content.get("to"), body)
        if msg is None:
            await self.send_json({"type": "error", "error": "You cannot message that person."})
            return
        payload = msg.as_dict()
        payload["client_id"] = str(content.get("client_id", ""))[:40]
        for group in {self.user_group, f"user_{msg.recipient_id}"}:
            await self.channel_layer.group_send(group, {"type": "chat.message", "payload": payload})

    # ---- group event handlers (server -> this socket) ----
    async def chat_message(self, event):
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

    @database_sync_to_async
    def _valid_peer(self, peer_id):
        """Return peer id only if it is another ACTIVE user of the SAME company (isolation)."""
        try:
            peer_id = int(peer_id)
        except (TypeError, ValueError):
            return None
        ok = User.objects.filter(pk=peer_id, company_id=self.cid, is_active=True).exclude(pk=self.uid).exists()
        return peer_id if ok else None

    @database_sync_to_async
    def _save(self, peer_id, body):
        try:
            peer_id = int(peer_id)
        except (TypeError, ValueError):
            return None
        # Re-check sender is still active (admin may have deactivated them since connecting).
        if not User.objects.filter(pk=self.uid, is_active=True).exists():
            return None
        peer = User.objects.filter(pk=peer_id, company_id=self.cid, is_active=True).exclude(pk=self.uid).first()
        if not peer:
            return None
        return Message.objects.create(company_id=self.cid, sender_id=self.uid, recipient=peer, body=body)

    @database_sync_to_async
    def _mark_read(self, peer_id):
        try:
            peer_id = int(peer_id)
        except (TypeError, ValueError):
            return
        Message.objects.filter(company_id=self.cid, sender_id=peer_id, recipient_id=self.uid,
                               read_at__isnull=True).update(read_at=timezone.now())
