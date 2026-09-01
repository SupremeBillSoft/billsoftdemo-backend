# bank/urls.py
from rest_framework.routers import DefaultRouter

from .views import BankViewSet

router = DefaultRouter()
router.register("banks", BankViewSet, basename="bank")
urlpatterns = router.urls
