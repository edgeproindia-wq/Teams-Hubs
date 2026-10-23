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
    # True for accounts created by an Excel import: they must pick their own password at first login.
    must_change_password = models.BooleanField(default=False)

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


class Group(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="groups")
    name = models.CharField(max_length=80)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="created_groups")
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name

    @property
    def initials(self):
        parts = self.name.split()
        return ("".join(p[0] for p in parts[:2]) or "#").upper()


class GroupMember(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="group_memberships")
    joined_at = models.DateTimeField(default=timezone.now)
    last_read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="uniq_group_member")]


class Message(models.Model):
    """A private message (recipient set) or a group message (group set) - never both."""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_messages")
    recipient = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE,
                                  related_name="received_messages")
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.CASCADE, related_name="messages")
    body = models.TextField(max_length=2000, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)       # private messages only ("seen")
    deleted_at = models.DateTimeField(null=True, blank=True)    # set when the sender deletes it

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["company", "sender", "recipient", "created_at"])]
        constraints = [models.CheckConstraint(
            condition=(models.Q(recipient__isnull=False, group__isnull=True)
                       | models.Q(recipient__isnull=True, group__isnull=False)),
            name="message_private_xor_group")]

    def __str__(self):
        return f"{self.sender_id}->{self.recipient_id or ('g' + str(self.group_id))}: {self.body[:30]}"

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def as_dict(self):
        return {
            "type": "message",
            "id": self.id,
            "sender": self.sender_id,
            "sender_name": self.sender.display_name,
            "recipient": self.recipient_id,
            "group": self.group_id,
            "body": "" if self.is_deleted else self.body,
            "deleted": self.is_deleted,
            "read": self.read_at is not None,
            "created_at": self.created_at.isoformat(),
        }
