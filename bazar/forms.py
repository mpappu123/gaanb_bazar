from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from .models import phone_validator

User = get_user_model()


class RegisterForm(forms.Form):
    name = forms.CharField(max_length=100)
    phone = forms.CharField(
        max_length=10, validators=[phone_validator], label="Mobile number",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "username"}),
    )
    address = forms.CharField(max_length=250, label="Village / area / address")
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        if User.objects.filter(username=phone).exists():
            raise forms.ValidationError("This number is already registered. Please log in.")
        return phone

    def clean(self):
        data = super().clean()
        pw = data.get("password")
        if pw:
            if pw != data.get("password2"):
                self.add_error("password2", "Passwords do not match.")
            else:
                validate_password(pw)   # uses AUTH_PASSWORD_VALIDATORS from settings
        return data


class PhoneLoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Mobile number", max_length=10,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )