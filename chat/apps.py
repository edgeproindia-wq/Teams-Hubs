import logging
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _create_default_admin(sender, using=None, **kwargs):
    if "test" in sys.argv or using != "default":
        return
    try:
        from .bootstrap import ensure_default_admin
        ensure_default_admin()
    except Exception:          # never break a deploy because of this helper
        logger.exception("could not create the default admin")


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_create_default_admin, sender=self)
