# currency/urls.py
from rest_framework.routers import DefaultRouter

from .views import CurrencyViewSet

router = DefaultRouter()
router.register("currencies", CurrencyViewSet, basename="currency")
urlpatterns = router.urls
