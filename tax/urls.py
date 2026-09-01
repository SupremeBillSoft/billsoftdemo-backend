# tax/urls.py
from rest_framework.routers import DefaultRouter

from .views import TaxViewSet

router = DefaultRouter()
router.register("taxes", TaxViewSet, basename="tax")
urlpatterns = router.urls
