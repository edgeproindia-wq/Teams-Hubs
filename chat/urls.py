from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("login/", views.RateLimitedLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("chat/", views.chat, name="chat"),
    path("directory/", views.directory, name="directory"),
    path("api/employees/", views.api_employees, name="api_employees"),
    path("api/messages/<int:peer_id>/", views.api_messages, name="api_messages"),
    path("api/copilot/", views.api_copilot, name="api_copilot"),
    path("admin-panel/", views.admin_panel, name="admin_panel"),
    path("admin-panel/messages/", views.admin_messages, name="admin_messages"),
    path("admin-panel/regenerate-code/", views.admin_regenerate_code, name="admin_regenerate_code"),
    path("admin-panel/user/<int:user_id>/", views.admin_user_action, name="admin_user_action"),
]
