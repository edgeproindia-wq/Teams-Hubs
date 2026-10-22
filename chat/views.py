import json
import logging
import time

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import ai
from .forms import LoginForm, RegisterForm
from .models import Message, User
from .permissions import api_login_required, company_admin_required

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


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(request.POST or None, initial={"mode": request.GET.get("mode", "create")})
    if request.method == "POST" and form.is_valid():
        user = form.save()
        auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, f"Welcome, {user.first_name}! Your account is ready.")
        return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form})


# ---------------------------------------------------------------- helpers
def _peers(user):
    """All other ACTIVE people in the user's own company. Every query goes through here."""
    return User.objects.filter(company_id=user.company_id, is_active=True).exclude(pk=user.pk)


def _peer_json(u, unread=0, last=None):
    return {
        "id": u.id, "name": u.display_name, "initials": u.initials, "job_title": u.job_title,
        "department": u.department, "role": u.role, "online": u.is_online, "unread": unread,
        "last_message": (last.body[:60] if last else ""),
        "last_at": (last.created_at.isoformat() if last else None),
    }


# ---------------------------------------------------------------- pages
@login_required
def dashboard(request):
    me = request.user
    peers = _peers(me)
    now = timezone.now()
    online = peers.filter(User.online_q()).order_by("first_name")
    my_msgs = Message.objects.filter(company_id=me.company_id).filter(Q(sender=me) | Q(recipient=me))
    ctx = {
        "stats": {
            "employees": peers.count() + 1,
            "online": online.count() + 1,
            "unread": Message.objects.filter(recipient=me, read_at__isnull=True).count(),
            "today": my_msgs.filter(created_at__date=timezone.localdate(now)).count(),
        },
        "online_users": online[:12],
        "recent": _recent_conversations(me)[:6],
        "page": "dashboard",
    }
    return render(request, "chat/dashboard.html", ctx)


def _recent_conversations(me):
    seen, out = set(), []
    qs = (Message.objects.filter(company_id=me.company_id).filter(Q(sender=me) | Q(recipient=me))
          .select_related("sender", "recipient").order_by("-created_at")[:200])
    for m in qs:
        peer = m.recipient if m.sender_id == me.id else m.sender
        if peer.id in seen or not peer.is_active:
            continue
        seen.add(peer.id)
        out.append({"peer": peer, "last": m, "mine": m.sender_id == me.id})
    return out


@login_required
def chat(request):
    return render(request, "chat/chat.html", {
        "page": "chat", "with_id": request.GET.get("with", ""), "ai_enabled": ai.ai_enabled()})


@login_required
def directory(request):
    return render(request, "chat/directory.html", {"page": "directory"})


# ---------------------------------------------------------------- JSON API
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

    unread = dict(Message.objects.filter(recipient=me, read_at__isnull=True)
                  .values_list("sender").annotate(c=Count("id")))
    last = {}
    for m in (Message.objects.filter(company_id=me.company_id).filter(Q(sender=me) | Q(recipient=me))
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
    Message.objects.filter(sender=peer, recipient=me, read_at__isnull=True).update(read_at=timezone.now())
    qs = Message.objects.filter(company_id=me.company_id).filter(
        Q(sender=me, recipient=peer) | Q(sender=peer, recipient=me)).order_by("-created_at")[:200]
    msgs = [m.as_dict() for m in reversed(list(qs))]
    return JsonResponse({"peer": _peer_json(peer), "messages": msgs})


@api_login_required
@require_POST
def api_copilot(request):
    me = request.user
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Bad request."}, status=400)
    action = data.get("action")
    tone = data.get("tone", "professional")
    text = str(data.get("text", "")).strip()[:2000]
    if action not in ai.ACTIONS or tone not in ai.TONES:
        return JsonResponse({"error": "Unknown action."}, status=400)

    # light rate limit: 20 requests / minute / user
    key = f"copilot:{me.id}:{int(time.time() // 60)}"
    n = cache.get(key, 0) + 1
    cache.set(key, n, 70)
    if n > 20:
        return JsonResponse({"error": "Slow down a little - try again in a minute."}, status=429)

    peer_name, context = "your colleague", []
    peer_id = data.get("peer_id")
    if peer_id:
        peer = _peers(me).filter(pk=peer_id).first()  # same-company only
        if peer:
            peer_name = peer.display_name
            recent = Message.objects.filter(company_id=me.company_id).filter(
                Q(sender=me, recipient=peer) | Q(sender=peer, recipient=me)).order_by("-created_at")[:6]
            for m in reversed(list(recent)):
                context.append(f"{'Me' if m.sender_id == me.id else peer.display_name}: {m.body[:300]}")
    if action in ("improve", "shorten", "draft") and not text:
        return JsonResponse({"error": "Type something in the message box first."}, status=400)
    if action == "reply" and not context and not text:
        return JsonResponse({"error": "There is no message to reply to yet."}, status=400)
    return JsonResponse(ai.compose(action, text, tone, peer_name, context))


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
            "online": users.filter(User.online_q(), is_active=True).exclude(pk=me.pk).count() + 1,  # viewer is online by definition
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
@require_POST
def admin_regenerate_code(request):
    request.user.company.regenerate_code()
    messages.success(request, "A new join code was generated. The old code no longer works.")
    return redirect("admin_panel")


@company_admin_required
def admin_messages(request):
    """Read-only audit view of every message in the admin's OWN company."""
    me = request.user
    qs = Message.objects.filter(company=me.company).select_related("sender", "recipient").order_by("-created_at")
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
