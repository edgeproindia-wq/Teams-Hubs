import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def notify(user_ids, payload):
    """Push a small JSON event to every open tab of the given users (best effort)."""
    layer = get_channel_layer()
    if layer is None:
        return
    for uid in set(user_ids):
        try:
            async_to_sync(layer.group_send)(f"user_{uid}", {"type": "chat.event", "payload": payload})
        except Exception:  # never fail a page because the live push failed
            logger.exception("live notification failed for user %s", uid)
