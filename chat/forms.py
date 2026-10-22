from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import Company, User


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label="Work email", widget=forms.EmailInput(
        attrs={"autofocus": True, "autocomplete": "username", "placeholder": "you@company.com"}))
    password = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput(
        attrs={"autocomplete": "current-password", "placeholder": "Your password"}))

    error_messages = {
        "invalid_login": "Incorrect email or password.",
        "inactive": "This account has been deactivated. Please contact your company admin.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class RegisterForm(forms.Form):
    MODE_CHOICES = [("create", "Create a new company"), ("join", "Join my company")]

    mode = forms.ChoiceField(choices=MODE_CHOICES, initial="create", widget=forms.RadioSelect)
    company_name = forms.CharField(max_length=120, required=False, label="Company name")
    join_code = forms.CharField(max_length=8, required=False, label="Company join code")
    full_name = forms.CharField(max_length=150, label="Full name")
    email = forms.EmailField(label="Work email")
    job_title = forms.CharField(max_length=100, required=False, label="Job title")
    department = forms.CharField(max_length=100, required=False, label="Department")
    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password"}))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists. Please log in.")
        return email

    def clean(self):
        data = super().clean()
        mode = data.get("mode")
        if mode == "create":
            if not (data.get("company_name") or "").strip():
                self.add_error("company_name", "Enter your company name.")
        elif mode == "join":
            code = (data.get("join_code") or "").strip().upper()
            company = Company.objects.filter(join_code=code).first() if code else None
            if not company:
                self.add_error("join_code", "That join code is not valid. Ask your company admin for it.")
            data["company"] = company
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            try:
                temp = User(username=data.get("email", ""), email=data.get("email", ""),
                            first_name=(data.get("full_name") or "")[:30])
                password_validation.validate_password(p1, temp)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self):
        d = self.cleaned_data
        full = " ".join(d["full_name"].split())
        first, _, last = full.partition(" ")
        if d["mode"] == "create":
            name = " ".join(d["company_name"].split())
            company = Company.objects.create(name=name, slug=Company.make_slug(name))
            role = User.ADMIN
        else:
            company, role = d["company"], User.EMPLOYEE
        user = User(username=d["email"], email=d["email"], first_name=first, last_name=last,
                    company=company, role=role, job_title=d.get("job_title", ""),
                    department=d.get("department", ""))
        user.set_password(d["password1"])
        user.save()
        return user
