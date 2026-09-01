# config/urls.py

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("parties.urls")),
    path("api/", include("items.urls")),
    path("api/", include("units.urls")),
    path("api/", include("tax.urls")),
    path("api/", include("bank.urls")),
    path("api/", include("currency.urls")),
    path("api/", include("branch.urls")),
    path("api/", include("hsn.urls")),
    path("api/", include("purchase.urls"))
]
