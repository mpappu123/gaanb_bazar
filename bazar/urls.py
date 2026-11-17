from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views
from .forms import PhoneLoginForm

urlpatterns = [
    path("", views.home, name="home"),
    path("order/", views.create_order, name="create_order"),
    path("register/", views.register, name="register"),
    path("login/", LoginView.as_view(template_name="store/login.html", authentication_form=PhoneLoginForm), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
