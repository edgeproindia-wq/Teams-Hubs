import json
from unittest import mock

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .models import Company, Message, User
from .routing import websocket_urlpatterns


def make_company(name="Acme"):
    return Company.objects.create(name=name, slug=Company.make_slug(name))


def make_user(company, email, role=User.EMPLOYEE, first="Test", last="User", pw="Str0ng-pass-99"):
    u = User(username=email, email=email, first_name=first, last_name=last, company=company, role=role)
    u.set_password(pw)
    u.save()
    return u


class RegistrationTests(TestCase):
    def payload(self, **kw):
        d = {"mode": "create", "company_name": "Acme Ltd", "join_code": "", "full_name": "Asha Rao",
             "email": "asha@acme.com", "job_title": "CEO", "department": "Mgmt",
             "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"}
        d.update(kw)
        return d

    def test_create_company_makes_admin_and_logs_in(self):
        r = self.client.post(reverse("register"), self.payload())
        self.assertRedirects(r, reverse("dashboard"))
        u = User.objects.get(email="asha@acme.com")
        self.assertEqual(u.role, User.ADMIN)
        self.assertEqual(u.company.name, "Acme Ltd")
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_join_with_code_makes_employee(self):
        c = make_company()
        r = self.client.post(reverse("register"), self.payload(
            mode="join", join_code=c.join_code.lower(), email="ravi@acme.com", full_name="Ravi K"))
        self.assertRedirects(r, reverse("dashboard"))
        u = User.objects.get(email="ravi@acme.com")
        self.assertEqual((u.company_id, u.role), (c.id, User.EMPLOYEE))

    def test_bad_join_code_rejected(self):
        r = self.client.post(reverse("register"), self.payload(mode="join", join_code="ZZZZZZZZ"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_duplicate_email_and_weak_password_rejected(self):
        make_user(make_company(), "asha@acme.com")
        self.assertFalse(self.client.post(reverse("register"), self.payload()).status_code == 302)
        r = self.client.post(reverse("register"), self.payload(email="new@acme.com", password1="12345678", password2="12345678"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@acme.com").exists())

    def test_password_mismatch_rejected(self):
        r = self.client.post(reverse("register"), self.payload(password2="different-pass-1"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.exists())


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = make_user(make_company(), "a@acme.com")

    def test_login_ok_and_logout(self):
        r = self.client.post(reverse("login"), {"username": "A@Acme.com", "password": "Str0ng-pass-99"})
        self.assertRedirects(r, reverse("dashboard"))
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            r = self.client.post(reverse("login"), {"username": "a@acme.com", "password": "nope"})
            self.assertContains(r, "Incorrect email or password")
        r = self.client.post(reverse("login"), {"username": "a@acme.com", "password": "Str0ng-pass-99"})
        self.assertContains(r, "Too many failed attempts")

    def test_deactivated_user_cannot_login(self):
        self.u.is_active = False
        self.u.save()
        r = self.client.post(reverse("login"), {"username": "a@acme.com", "password": "Str0ng-pass-99"})
        self.assertEqual(r.status_code, 200)

    def test_pages_require_login(self):
        for name in ("dashboard", "chat", "directory", "api_employees", "admin_panel"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302 if name != "api_employees" else 401, name)

    def test_health(self):
        self.assertEqual(self.client.get("/health/").content, b"ok")


class IsolationAndApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b = make_company("A Co"), make_company("B Co")
        self.a1 = make_user(self.a, "a1@a.com", first="Anil")
        self.a2 = make_user(self.a, "a2@a.com", first="Bina")
        self.b1 = make_user(self.b, "b1@b.com", first="Chen")
        Message.objects.create(company=self.a, sender=self.a1, recipient=self.a2, body="hello <b>bina</b>")
        Message.objects.create(company=self.b, sender=self.b1, recipient=self.b1, body="secret")
        self.client.force_login(self.a1)

    def test_directory_lists_only_own_company(self):
        names = [e["name"] for e in self.client.get(reverse("api_employees")).json()["employees"]]
        self.assertEqual(names, ["Bina User"])

    def test_search_by_name(self):
        self.assertEqual(len(self.client.get(reverse("api_employees"), {"q": "bin"}).json()["employees"]), 1)
        self.assertEqual(len(self.client.get(reverse("api_employees"), {"q": "chen"}).json()["employees"]), 0)

    def test_cannot_read_other_company_history(self):
        self.assertEqual(self.client.get(reverse("api_messages", args=[self.b1.id])).status_code, 404)

    def test_history_and_mark_read(self):
        self.client.force_login(self.a2)
        j = self.client.get(reverse("api_messages", args=[self.a1.id])).json()
        self.assertEqual(j["messages"][0]["body"], "hello <b>bina</b>")  # raw text; browser uses textContent
        self.assertIsNotNone(Message.objects.get(sender=self.a1).read_at)

    def test_online_filter(self):
        from django.utils import timezone
        self.a2.connections, self.a2.last_seen = 1, timezone.now()
        self.a2.save()
        self.assertEqual(len(self.client.get(reverse("api_employees"), {"online": "1"}).json()["employees"]), 1)
        self.a2.last_seen = timezone.now() - timezone.timedelta(minutes=10)
        self.a2.save()
        self.assertEqual(len(self.client.get(reverse("api_employees"), {"online": "1"}).json()["employees"]), 0)


@override_settings(ANTHROPIC_API_KEY="")
class CopilotTests(TestCase):
    def setUp(self):
        cache.clear()
        c = make_company()
        self.me, self.peer = make_user(c, "me@a.com", first="Me"), make_user(c, "p@a.com", first="Priya")
        self.other = make_user(make_company("Other"), "o@o.com")
        self.client.force_login(self.me)

    def post(self, **d):
        return self.client.post(reverse("api_copilot"), json.dumps(d), content_type="application/json")

    def test_offline_draft(self):
        r = self.post(action="draft", text="please send the report today", tone="professional", peer_id=self.peer.id)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["source"], "offline")
        self.assertIn("Priya", r.json()["text"])

    def test_empty_text_and_bad_action(self):
        self.assertEqual(self.post(action="improve", text="", tone="formal").status_code, 400)
        self.assertEqual(self.post(action="hack", text="x").status_code, 400)

    @override_settings(ANTHROPIC_API_KEY="k")
    def test_ai_success_and_failure_fallback(self):
        with mock.patch("chat.ai._call_claude", return_value="Polished."):
            self.assertEqual(self.post(action="improve", text="hi").json(), {"text": "Polished.", "source": "ai"})
        with mock.patch("chat.ai._call_claude", side_effect=RuntimeError("down")):
            r = self.post(action="improve", text="hi there")
            self.assertEqual(r.json()["source"], "offline")

    def test_other_company_peer_gives_no_context(self):
        Message.objects.create(company=self.other.company, sender=self.other, recipient=self.other, body="private")
        with mock.patch("chat.ai.compose", return_value={"text": "x", "source": "ai"}) as m:
            self.post(action="draft", text="hi", peer_id=self.other.id)
        self.assertEqual(m.call_args[0][4], [])

    def test_rate_limit(self):
        for _ in range(20):
            self.post(action="draft", text="hi")
        self.assertEqual(self.post(action="draft", text="hi").status_code, 429)


class AdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c, self.other = make_company("A"), make_company("B")
        self.admin = make_user(self.c, "admin@a.com", role=User.ADMIN, first="Boss")
        self.emp = make_user(self.c, "emp@a.com", first="Emp")
        self.stranger = make_user(self.other, "s@b.com", role=User.ADMIN, first="Stranger")
        Message.objects.create(company=self.c, sender=self.emp, recipient=self.admin, body="visible to admin")
        Message.objects.create(company=self.other, sender=self.stranger, recipient=self.stranger, body="other co")

    def test_employee_forbidden(self):
        self.client.force_login(self.emp)
        for name in ("admin_panel", "admin_messages"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin_user_action", args=[self.admin.id]),
                                          {"action": "toggle_active"}).status_code, 403)

    def test_admin_sees_company_data_only(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin_panel"))
        self.assertContains(r, "emp@a.com")
        self.assertNotContains(r, "s@b.com")
        r = self.client.get(reverse("admin_messages"))
        self.assertContains(r, "visible to admin")
        self.assertNotContains(r, "other co")

    def test_deactivate_and_reactivate_employee(self):
        self.client.force_login(self.admin)
        url = reverse("admin_user_action", args=[self.emp.id])
        self.client.post(url, {"action": "toggle_active"})
        self.emp.refresh_from_db()
        self.assertFalse(self.emp.is_active)
        self.client.post(url, {"action": "toggle_active"})
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.is_active)

    def test_cannot_touch_other_company_user(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("admin_user_action", args=[self.stranger.id]), {"action": "toggle_active"})
        self.assertEqual(r.status_code, 404)
        self.stranger.refresh_from_db()
        self.assertTrue(self.stranger.is_active)

    def test_last_admin_protected_and_self_deactivate_blocked(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_user_action", args=[self.admin.id]), {"action": "toggle_active"})
        self.client.post(reverse("admin_user_action", args=[self.admin.id]), {"action": "toggle_role"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertEqual(self.admin.role, User.ADMIN)

    def test_promote_employee_and_new_code(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_user_action", args=[self.emp.id]), {"action": "toggle_role"})
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.role, User.ADMIN)
        old = self.c.join_code
        self.client.post(reverse("admin_regenerate_code"))
        self.c.refresh_from_db()
        self.assertNotEqual(old, self.c.join_code)

    def test_message_filters(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("admin_messages"), {"a": self.emp.id}), "visible to admin")
        self.assertNotContains(self.client.get(reverse("admin_messages"), {"q": "zzz"}), "visible to admin")


class SocketTests(TransactionTestCase):
    """Real-time behaviour through the actual consumer."""

    def setUp(self):
        self.a, self.b = make_company("A"), make_company("B")
        self.u1 = make_user(self.a, "u1@a.com", first="One")
        self.u2 = make_user(self.a, "u2@a.com", first="Two")
        self.x = make_user(self.b, "x@b.com", first="Ex")

    async def _connect(self, user):
        comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), "/ws/chat/")
        comm.scope["user"] = user
        ok, _ = await comm.connect()
        return comm, ok

    async def _drain(self, comm):
        out = []
        while not await comm.receive_nothing(timeout=0.2):
            out.append(await comm.receive_json_from())
        return out

    async def test_private_message_delivered_and_saved(self):
        c1, ok1 = await self._connect(self.u1)
        c2, ok2 = await self._connect(self.u2)
        self.assertTrue(ok1 and ok2)
        await self._drain(c1)
        await self._drain(c2)
        await c1.send_json_to({"type": "message", "to": self.u2.id, "body": "  hi there  "})
        got = [m for m in await self._drain(c2) if m["type"] == "message"]
        mine = [m for m in await self._drain(c1) if m["type"] == "message"]
        self.assertEqual(got[0]["body"], "hi there")
        self.assertEqual(got[0]["sender"], self.u1.id)
        self.assertEqual(mine[0]["body"], "hi there")  # sender gets an echo too (multi-tab sync)
        self.assertEqual(await Message.objects.acount(), 1)
        await c1.disconnect()
        await c2.disconnect()

    async def test_sender_also_gets_echo(self):
        c1, _ = await self._connect(self.u1)
        await self._drain(c1)
        await c1.send_json_to({"type": "message", "to": self.u2.id, "body": "echo"})
        msgs = [m for m in await self._drain(c1) if m["type"] == "message"]
        self.assertEqual(msgs[0]["body"], "echo")
        await c1.disconnect()

    async def test_cross_company_message_refused(self):
        c1, _ = await self._connect(self.u1)
        cx, _ = await self._connect(self.x)
        await self._drain(c1)
        await self._drain(cx)
        await c1.send_json_to({"type": "message", "to": self.x.id, "body": "leak?"})
        self.assertEqual((await c1.receive_json_from())["type"], "error")
        self.assertEqual([m for m in await self._drain(cx) if m["type"] == "message"], [])
        self.assertEqual(await Message.objects.acount(), 0)
        await c1.disconnect()
        await cx.disconnect()

    async def test_invalid_messages_refused(self):
        c1, _ = await self._connect(self.u1)
        await self._drain(c1)
        for body in ("", "   ", "x" * 2001):
            await c1.send_json_to({"type": "message", "to": self.u2.id, "body": body})
            self.assertEqual((await c1.receive_json_from())["type"], "error")
        await c1.send_json_to({"type": "message", "to": self.u1.id, "body": "to self"})
        self.assertEqual((await c1.receive_json_from())["type"], "error")
        await c1.disconnect()

    async def test_presence_online_then_offline(self):
        c2, _ = await self._connect(self.u2)
        await self._drain(c2)
        c1, _ = await self._connect(self.u1)
        events = await self._drain(c2)
        self.assertIn({"type": "presence", "user_id": self.u1.id, "online": True}, events)
        await c1.disconnect()
        events = await self._drain(c2)
        self.assertIn({"type": "presence", "user_id": self.u1.id, "online": False}, events)
        await c2.disconnect()

    async def test_two_tabs_stay_online_until_last_closes(self):
        c2, _ = await self._connect(self.u2)
        await self._drain(c2)
        t1, _ = await self._connect(self.u1)
        t2, _ = await self._connect(self.u1)
        await self._drain(c2)
        await t1.disconnect()
        self.assertIn({"type": "presence", "user_id": self.u1.id, "online": True}, await self._drain(c2))
        await t2.disconnect()
        self.assertIn({"type": "presence", "user_id": self.u1.id, "online": False}, await self._drain(c2))
        await c2.disconnect()

    async def test_typing_and_cross_company_typing(self):
        c1, _ = await self._connect(self.u1)
        c2, _ = await self._connect(self.u2)
        cx, _ = await self._connect(self.x)
        for c in (c1, c2, cx):
            await self._drain(c)
        await c1.send_json_to({"type": "typing", "to": self.u2.id})
        await c1.send_json_to({"type": "typing", "to": self.x.id})
        self.assertIn({"type": "typing", "from": self.u1.id}, await self._drain(c2))
        self.assertEqual(await self._drain(cx), [])
        for c in (c1, c2, cx):
            await c.disconnect()

    async def test_anonymous_rejected(self):
        from django.contrib.auth.models import AnonymousUser
        comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), "/ws/chat/")
        comm.scope["user"] = AnonymousUser()
        ok, _ = await comm.connect()
        self.assertFalse(ok)
