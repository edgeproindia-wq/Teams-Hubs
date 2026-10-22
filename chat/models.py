import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

# A browser tab sends a heartbeat every 25s; no heartbeat for this long means "offline".
PRESENCE_STALE_SECONDS = 90
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid typos


def new_join_code():
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


class Company(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    join_code = models.CharField(max_length=8, unique=True, default=new_join_code)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name

    @staticmethod
    def make_slug(name):
        base = slugify(name)[:100] or "company"
        slug, n = base, 1
        while Company.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base}-{n}"
        return slug

    def regenerate_code(self):
        self.join_code = new_join_code()
        self.save(update_fields=["join_code"])


class User(AbstractUser):
    ADMIN = "ADMIN"
    EMPLOYEE = "EMPLOYEE"
    ROLE_CHOICES = [(ADMIN, "Company admin"), (EMPLOYEE, "Employee")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="users", null=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=EMPLOYEE)
    job_title = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    # Number of open websocket connections (tabs/devices) for this user.
    connections = models.PositiveIntegerField(default=0)
    last_seen = models.DateTimeField(null=True, blank=True)

    @property
    def display_name(self):
        return self.get_full_name().strip() or self.email or self.username

    @property
    def initials(self):
        parts = self.display_name.split()
        return ("".join(p[0] for p in parts[:2]) or "?").upper()

    @property
    def is_company_admin(self):
        return self.role == self.ADMIN

    @staticmethod
    def online_q():
        cutoff = timezone.now() - timedelta(seconds=PRESENCE_STALE_SECONDS)
        return Q(connections__gt=0, last_seen__gte=cutoff)

    @property
    def is_online(self):
        return bool(
            self.connections > 0 and self.last_seen
            and self.last_seen >= timezone.now() - timedelta(seconds=PRESENCE_STALE_SECONDS)
        )


class Message(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_messages")
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="received_messages")
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["company", "sender", "recipient", "created_at"])]

    def __str__(self):
        return f"{self.sender_id}->{self.recipient_id}: {self.body[:30]}"

    def as_dict(self):
        return {
            "type": "message",
            "id": self.id,
            "sender": self.sender_id,
            "recipient": self.recipient_id,
            "body": self.body,
            "created_at": self.created_at.isoformat(),
        }
