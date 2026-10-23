from django import forms
from django.contrib.auth.forms import AuthenticationForm


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="Login ID", max_length=150, widget=forms.TextInput(
        attrs={"autofocus": True, "autocomplete": "username", "autocapitalize": "none",
               "placeholder": "Login ID or work email"}))
    password = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput(
        attrs={"autocomplete": "current-password", "placeholder": "Your password"}))

    error_messages = {
        "invalid_login": "Incorrect Login ID or password.",
        "inactive": "This account has been deactivated. Please contact your company admin.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()
