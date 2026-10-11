# common/gst/urls.py

from django.urls import path

from .views import GstLineCalculationView

urlpatterns = [
    path("gst/calculate-line/", GstLineCalculationView.as_view(), name="gst-calculate-line"),
]

# Wire into the project's root urls.py, e.g.:
#   path("api/common/", include("common.gst.urls")),
