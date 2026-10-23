from django.contrib.auth.views import LogoutView
from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("login/", views.RateLimitedLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", RedirectView.as_view(pattern_name="login"), name="register"),   # self-registration is closed
    path("password/", views.ChangePasswordView.as_view(), name="password_change"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("chat/", views.chat, name="chat"),
    path("directory/", views.directory, name="directory"),
    path("api/employees/", views.api_employees, name="api_employees"),
    path("api/messages/<int:peer_id>/", views.api_messages, name="api_messages"),
    path("api/groups/", views.api_groups, name="api_groups"),
    path("api/groups/<int:gid>/", views.api_group_detail, name="api_group_detail"),
    path("api/groups/<int:gid>/members/", views.api_group_add, name="api_group_add"),
    path("api/groups/<int:gid>/members/<int:uid>/remove/", views.api_group_remove, name="api_group_remove"),
    path("admin-panel/", views.admin_panel, name="admin_panel"),
    path("admin-panel/messages/", views.admin_messages, name="admin_messages"),
    path("admin-panel/import/", views.admin_import, name="admin_import"),
    path("admin-panel/import/template/", views.admin_import_template, name="admin_import_template"),
    path("admin-panel/user/<int:user_id>/", views.admin_user_action, name="admin_user_action"),
]
