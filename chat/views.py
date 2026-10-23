import base64
import json
import logging

from django.contrib import messages
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import importer
from .forms import LoginForm
from .models import Group, GroupMember, Message, User
from .permissions import api_login_required, company_admin_required, member_required
from .realtime import notify

logger = logging.getLogger(__name__)

LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 15 * 60


def health(request):
    return HttpResponse("ok")


def home(request):
    return redirect("dashboard" if request.user.is_authenticated else "login")


# ---------------------------------------------------------------- auth
def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")) or "?"


class RateLimitedLoginView(LoginView):
    """Login with a simple brute-force lock: 5 wrong tries -> 15 minutes."""
    form_class = LoginForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def _key(self, request):
        email = (request.POST.get("username") or "").strip().lower()
        return f"loginfail:{_client_ip(request)}:{email}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(request), 0) >= LOGIN_MAX_FAILS:
            form = self.get_form()
            form.add_error(None, "Too many failed attempts. Please try again in 15 minutes.")
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key(self.request)
        cache.set(key, cache.get(key, 0) + 1, LOGIN_LOCK_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key(self.request))
        return super().form_valid(form)


# ---------------------------------------------------------------- helpers
def _peers(user):
    """All other ACTIVE people in the user's own company. Every query goes through here."""
    return User.objects.filter(company_id=user.company_id, is_active=True).exclude(pk=user.pk)


def _preview(m):
    return "This message was deleted" if m.is_deleted else m.body[:60]


def _peer_json(u, unread=0, last=None):
    return {
        "id": u.id, "name": u.display_name, "initials": u.initials, "job_title": u.job_title,
        "department": u.department, "role": u.role, "online": u.is_online, "unread": unread,
        "last_message": (_preview(last) if last else ""),
        "last_at": (last.created_at.isoformat() if last else None),
    }


def _private(qs):
    return qs.filter(group__isnull=True)


# ---------------------------------------------------------------- pages
@member_required
def dashboard(request):
    me = request.user
    peers = _peers(me)
    now = timezone.now()
    online = peers.filter(User.online_q()).order_by("first_name")
    my_msgs = Message.objects.filter(company_id=me.company_id, deleted_at__isnull=True).filter(
        Q(sender=me) | Q(recipient=me))
    ctx = {
        "stats": {
            "employees": peers.count() + 1,
            "online": online.count() + 1,
            "unread": Message.objects.filter(recipient=me, read_at__isnull=True, deleted_at__isnull=True).count(),
            "today": my_msgs.filter(created_at__date=timezone.localdate(now)).count(),
        },
        "online_users": online[:12],
        "recent": _recent_conversations(me)[:6],
        "page": "dashboard",
    }
    return render(request, "chat/dashboard.html", ctx)


def _recent_conversations(me):
    seen, out = set(), []
    qs = (_private(Message.objects.filter(company_id=me.company_id)).filter(Q(sender=me) | Q(recipient=me))
          .select_related("sender", "recipient").order_by("-created_at")[:200])
    for m in qs:
        peer = m.recipient if m.sender_id == me.id else m.sender
        if peer.id in seen or not peer.is_active:
            continue
        seen.add(peer.id)
        out.append({"peer": peer, "last": m, "mine": m.sender_id == me.id,
                    "preview": _preview(m)})
    return out


@member_required
def chat(request):
    return render(request, "chat/chat.html", {
        "page": "chat", "with_id": request.GET.get("with", ""), "group_id": request.GET.get("group", "")})


@member_required
def directory(request):
    return render(request, "chat/directory.html", {"page": "directory"})


# ---------------------------------------------------------------- password change
class ChangePasswordView(PasswordChangeView):
    template_name = "registration/password_change.html"
    success_url = reverse_lazy("dashboard")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page"] = "password"
        return ctx

    def form_valid(self, form):
        resp = super().form_valid(form)
        u = self.request.user
        if u.must_change_password:
            u.must_change_password = False
            u.save(update_fields=["must_change_password"])
        messages.success(self.request, "Your password was changed.")
        return resp


# ---------------------------------------------------------------- JSON API: people + private messages
@api_login_required
@require_GET
def api_employees(request):
    me = request.user
    q = (request.GET.get("q") or "").strip()
    qs = _peers(me)
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q)
                       | Q(job_title__icontains=q) | Q(department__icontains=q))
    if request.GET.get("online") == "1":
        qs = qs.filter(User.online_q())
    qs = list(qs.order_by("first_name", "last_name")[:300])

    unread = dict(Message.objects.filter(recipient=me, read_at__isnull=True, deleted_at__isnull=True)
                  .values_list("sender").annotate(c=Count("id")))
    last = {}
    for m in (_private(Message.objects.filter(company_id=me.company_id)).filter(Q(sender=me) | Q(recipient=me))
              .order_by("-created_at")[:500]):
        pid = m.recipient_id if m.sender_id == me.id else m.sender_id
        last.setdefault(pid, m)

    data = [_peer_json(u, unread.get(u.id, 0), last.get(u.id)) for u in qs]
    # recent conversations first (newest on top), then everyone else A-Z
    talked = sorted([p for p in data if p["last_at"]], key=lambda p: p["last_at"], reverse=True)
    rest = sorted([p for p in data if not p["last_at"]], key=lambda p: p["name"].lower())
    return JsonResponse({"employees": talked + rest, "me": me.id})


@api_login_required
@require_GET
def api_messages(request, peer_id):
    me = request.user
    # Company isolation: a peer from another company simply does not exist for this user.
    peer = get_object_or_404(User, pk=peer_id, company_id=me.company_id)
    if peer.pk == me.pk:
        return JsonResponse({"error": "Not allowed."}, status=400)
    marked = Message.objects.filter(sender=peer, recipient=me, read_at__isnull=True).update(read_at=timezone.now())
    if marked:
        notify([peer.id], {"type": "seen", "by": me.id})   # blue ticks appear for the sender
    qs = (_private(Message.objects.filter(company_id=me.company_id))
          .filter(Q(sender=me, recipient=peer) | Q(sender=peer, recipient=me))
          .select_related("sender").order_by("-created_at")[:200])
    return JsonResponse({"peer": _peer_json(peer), "messages": [m.as_dict() for m in reversed(list(qs))]})


# ---------------------------------------------------------------- JSON API: groups
def _group_for(me, gid):
    """The group, only if it is in my company AND I am a member."""
    ms = get_object_or_404(GroupMember.objects.select_related("group"),
                           group_id=gid, user=me, group__company_id=me.company_id)
    return ms.group, ms


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _id_list(value, cap=200):
    out = []
    for v in (value if isinstance(value, list) else [])[:cap]:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            pass
    return out


@api_login_required
def api_groups(request):
    me = request.user
    if request.method == "POST":
        data = _body(request)
        name = " ".join(str(data.get("name", "")).split())[:80]
        if not name:
            return JsonResponse({"error": "Enter a group name."}, status=400)
        people = list(_peers(me).filter(pk__in=_id_list(data.get("member_ids"))))
        if not people:
            return JsonResponse({"error": "Pick at least one person for the group."}, status=400)
        with transaction.atomic():
            g = Group.objects.create(company=me.company, name=name, created_by=me)
            GroupMember.objects.bulk_create([GroupMember(group=g, user=u) for u in [me] + people])
        notify([u.id for u in people], {"type": "groups_changed"})
        return JsonResponse({"id": g.id}, status=201)
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    mems = list(GroupMember.objects.filter(user=me, group__company_id=me.company_id).select_related("group"))
    ids = [m.group_id for m in mems]
    counts = dict(GroupMember.objects.filter(group_id__in=ids).values_list("group_id").annotate(c=Count("id")))
    last = {}
    for m in Message.objects.filter(group_id__in=ids).select_related("sender").order_by("-created_at")[:500]:
        last.setdefault(m.group_id, m)
    out = []
    for ms in mems:
        g = ms.group
        lm = last.get(g.id)
        unread = Message.objects.filter(group=g, created_at__gt=ms.last_read_at, deleted_at__isnull=True
                                        ).exclude(sender=me).count()
        out.append({"id": g.id, "name": g.name, "initials": g.initials, "members": counts.get(g.id, 0),
                    "is_owner": g.created_by_id == me.id, "unread": unread,
                    "last_message": (f"{lm.sender.first_name}: {_preview(lm)}" if lm else ""),
                    "last_at": lm.created_at.isoformat() if lm else None})
    talked = sorted([g for g in out if g["last_at"]], key=lambda g: g["last_at"], reverse=True)
    rest = sorted([g for g in out if not g["last_at"]], key=lambda g: g["name"].lower())
    return JsonResponse({"groups": talked + rest})


@api_login_required
@require_GET
def api_group_detail(request, gid):
    me = request.user
    g, ms = _group_for(me, gid)
    members = [{"id": m.user_id, "name": m.user.display_name, "initials": m.user.initials,
                "job_title": m.user.job_title, "online": m.user.is_online}
               for m in GroupMember.objects.filter(group=g).select_related("user").order_by("joined_at")]
    msgs = Message.objects.filter(group=g).select_related("sender", "group").order_by("-created_at")[:200]
    GroupMember.objects.filter(pk=ms.pk).update(last_read_at=timezone.now())
    return JsonResponse({
        "group": {"id": g.id, "name": g.name, "initials": g.initials, "owner_id": g.created_by_id,
                  "is_owner": g.created_by_id == me.id, "members": members},
        "messages": [m.as_dict() for m in reversed(list(msgs))]})


@api_login_required
@require_POST
def api_group_add(request, gid):
    me = request.user
    g, _ = _group_for(me, gid)
    if g.created_by_id != me.id:
        return JsonResponse({"error": "Only the group owner can add people."}, status=403)
    existing = set(GroupMember.objects.filter(group=g).values_list("user_id", flat=True))
    new = [u for u in _peers(me).filter(pk__in=_id_list(_body(request).get("user_ids"))) if u.id not in existing]
    GroupMember.objects.bulk_create([GroupMember(group=g, user=u) for u in new])
    notify(list(existing | {u.id for u in new}), {"type": "groups_changed"})
    return JsonResponse({"added": len(new)})


@api_login_required
@require_POST
def api_group_remove(request, gid, uid):
    """The owner removes someone, or anyone removes themselves (leave)."""
    me = request.user
    g, _ = _group_for(me, gid)
    if uid != me.id and g.created_by_id != me.id:
        return JsonResponse({"error": "Only the group owner can remove people."}, status=403)
    target = get_object_or_404(GroupMember, group=g, user_id=uid)
    everyone = set(GroupMember.objects.filter(group=g).values_list("user_id", flat=True))
    target.delete()
    rest = GroupMember.objects.filter(group=g).order_by("joined_at")
    if not rest.exists():
        g.delete()                      # last person left
    elif g.created_by_id == uid:
        g.created_by_id = rest.first().user_id   # owner left: the longest-standing member takes over
        g.save(update_fields=["created_by"])
    notify(list(everyone), {"type": "groups_changed"})
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- company admin
@company_admin_required
def admin_panel(request):
    me = request.user
    company = me.company
    users = User.objects.filter(company=company).order_by("-is_active", "first_name")
    msgs = Message.objects.filter(company=company)
    ctx = {
        "page": "admin", "company": company, "users": users,
        "stats": {
            "total": users.count(),
            "active": users.filter(is_active=True).count(),
            "online": users.filter(User.online_q(), is_active=True).exclude(pk=me.pk).count() + 1,
            "messages": msgs.count(),
            "today": msgs.filter(created_at__date=timezone.localdate()).count(),
            "admins": users.filter(role=User.ADMIN, is_active=True).count(),
        },
    }
    return render(request, "chat/admin_panel.html", ctx)


@company_admin_required
@require_POST
def admin_user_action(request, user_id):
    me = request.user
    target = get_object_or_404(User, pk=user_id, company=me.company)  # ownership check, never id alone
    action = request.POST.get("action")
    active_admins = User.objects.filter(company=me.company, role=User.ADMIN, is_active=True)
    last_admin = target.role == User.ADMIN and target.is_active and active_admins.count() <= 1
    if action == "toggle_active":
        if target.pk == me.pk:
            messages.error(request, "You cannot deactivate your own account.")
        elif last_admin:
            messages.error(request, "A company must keep at least one active admin.")
        else:
            target.is_active = not target.is_active
            target.save(update_fields=["is_active"])
            messages.success(request, f"{target.display_name} is now {'active' if target.is_active else 'deactivated'}.")
    elif action == "toggle_role":
        if last_admin:
            messages.error(request, "A company must keep at least one active admin.")
        else:
            target.role = User.EMPLOYEE if target.role == User.ADMIN else User.ADMIN
            target.save(update_fields=["role"])
            messages.success(request, f"{target.display_name} is now {target.get_role_display()}.")
    else:
        messages.error(request, "Unknown action.")
    return redirect("admin_panel")


@company_admin_required
def admin_messages(request):
    """Read-only audit view of every message in the admin's OWN company."""
    me = request.user
    qs = (Message.objects.filter(company=me.company).select_related("sender", "recipient", "group")
          .order_by("-created_at"))
    people = User.objects.filter(company=me.company).order_by("first_name")
    a, b, q = request.GET.get("a", ""), request.GET.get("b", ""), (request.GET.get("q") or "").strip()
    if a.isdigit() and b.isdigit():
        qs = qs.filter(Q(sender_id=a, recipient_id=b) | Q(sender_id=b, recipient_id=a))
    elif a.isdigit():
        qs = qs.filter(Q(sender_id=a) | Q(recipient_id=a))
    if q:
        qs = qs.filter(body__icontains=q)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "chat/admin_messages.html", {
        "page": "admin", "msgs": page, "people": people, "a": a, "b": b, "q": q})


# ---------------------------------------------------------------- Excel import (company admin)
@company_admin_required
def admin_import(request):
    ctx = {"page": "admin", "max_rows": importer.MAX_ROWS}
    if request.method != "POST":
        return render(request, "chat/admin_import.html", ctx)
    f = request.FILES.get("file")
    if not f or not f.name.lower().endswith(".xlsx"):
        ctx["error"] = "Choose an Excel file ending in .xlsx."
        return render(request, "chat/admin_import.html", ctx)
    if f.size > importer.MAX_BYTES:
        ctx["error"] = "That file is too large (limit 2 MB)."
        return render(request, "chat/admin_import.html", ctx)
    try:
        rows = importer.read_rows(f)
    except importer.ImportProblem as exc:
        ctx["error"] = str(exc)
        return render(request, "chat/admin_import.html", ctx)
    created, skipped = importer.import_employees(request.user.company, rows)
    ctx.update({"created": created, "skipped": skipped})
    if created:
        blob = base64.b64encode(importer.credentials_xlsx(created)).decode()
        ctx["download"] = "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64," + blob
    return never_cache(lambda r: render(r, "chat/admin_import_result.html", ctx))(request)


@company_admin_required
def admin_import_template(request):
    resp = HttpResponse(importer.template_xlsx(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="employee_import_template.xlsx"'
    return resp
