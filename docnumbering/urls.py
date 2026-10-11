# docnumbering/urls.py

from rest_framework.routers import DefaultRouter

from .views import DocumentNumberFormatViewSet

router = DefaultRouter()
router.register("document-number-formats", DocumentNumberFormatViewSet, basename="document-number-format")

urlpatterns = router.urls

# Wire this into the project's root urls.py, e.g.:
#   path("api/", include("docnumbering.urls")),
