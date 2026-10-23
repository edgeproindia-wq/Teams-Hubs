"""Creates the default company admin (Login ID "bharath") automatically after every `migrate`.

Settings (all optional, set them in Render > Environment):
  DEFAULT_ADMIN_USERNAME  Login ID                    (default: bharath)
  DEFAULT_ADMIN_NAME      Display name                (default: Bharath)
  DEFAULT_ADMIN_PASSWORD  Temporary first password    (default: random, printed ONCE in the deploy log)
  DEFAULT_COMPANY_NAME    Company to put the admin in (default: the only company, else "Bharath")
  DEFAULT_ADMIN_RESET=1   Forgot the password? Set it, redeploy once, then REMOVE it.
  DISABLE_DEFAULT_ADMIN=1 Turn this feature off.
A password you set yourself in DEFAULT_ADMIN_PASSWORD is used as it is. A random one must be changed at first login.
An existing account is left alone - except that, until its first login, it picks up a changed DEFAULT_ADMIN_PASSWORD.
"""
import os
import secrets

from .models import Company, User

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


def _random_password():
    return "".join(secrets.choice(_ALPHABET) for _ in range(14))


def ensure_default_admin(out=print):
    env = os.environ.get
    if env("DISABLE_DEFAULT_ADMIN") == "1":
        return None
    username = (env("DEFAULT_ADMIN_USERNAME") or "bharath").strip().lower()
    name = " ".join((env("DEFAULT_ADMIN_NAME") or "Bharath").split())
    company_name = (env("DEFAULT_COMPANY_NAME") or "").strip()
    given = env("DEFAULT_ADMIN_PASSWORD") or ""
    reset = env("DEFAULT_ADMIN_RESET") == "1"

    user = User.objects.filter(username__iexact=username).first()
    if user:
        if reset:
            password = given or _random_password()
            user.set_password(password)
            user.is_active, user.role, user.must_change_password = True, User.ADMIN, not given
            user.save()
            out(f"EdgeChat: password reset for Login ID '{user.username}'."
                + ("" if given else f" Temporary password: {password}") + " REMOVE DEFAULT_ADMIN_RESET now.")
        elif given and user.last_login is None and not user.check_password(given):
            # nobody has logged in yet: make the account match the password now set in Render
            user.set_password(given)
            user.must_change_password = False
            user.save()
            out(f"EdgeChat: Login ID '{user.username}' now uses the password from DEFAULT_ADMIN_PASSWORD.")
        return None

    company = None
    if company_name:
        company = Company.objects.filter(name__iexact=company_name).first()
    elif Company.objects.count() == 1:
        company = Company.objects.first()
    if company is None:
        company_name = company_name or "Bharath"
        company = Company.objects.create(name=company_name, slug=Company.make_slug(company_name))

    password = given or _random_password()
    first, _, last = name.partition(" ")
    user = User(username=username, email="", first_name=first, last_name=last, company=company,
                role=User.ADMIN, must_change_password=not given)
    user.set_password(password)
    user.save()
    out(f"EdgeChat: created default admin. Login ID: {username}"
        + (" (password = DEFAULT_ADMIN_PASSWORD)" if given
           else f"  Temporary password: {password}  - you must change it at first login."))
    return user
