from functools import wraps

from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect


def member_required(view):
    """Signed-in, active company member. Imported accounts must first set their own password."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        u = request.user
        if not u.is_authenticated:
            return redirect("login")
        if not (u.is_active and u.company_id):
            return redirect("login")
        if u.must_change_password:
            return redirect("password_change")
        return view(request, *args, **kwargs)
    return wrapper


def api_login_required(view):
    """Like login_required but answers JSON so the browser code can handle it."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        u = request.user
        if not u.is_authenticated or not u.is_active or not u.company_id:
            return JsonResponse({"error": "Not signed in."}, status=401)
        if u.must_change_password:
            return JsonResponse({"error": "Please change your password first."}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


def company_admin_required(view):
    """Backend check - hiding the menu link alone is never enough."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        u = request.user
        if not u.is_authenticated:
            return redirect("login")
        if u.must_change_password:
            return redirect("password_change")
        if not (u.is_active and u.company_id and u.is_company_admin):
            return HttpResponseForbidden("Only company admins can open this page.")
        return view(request, *args, **kwargs)
    return wrapper
