# tenants/urls.py

from django.urls import path

from .views import CompanySettingsView

urlpatterns = [
    path("company-settings/", CompanySettingsView.as_view(), name="company-settings"),
]
