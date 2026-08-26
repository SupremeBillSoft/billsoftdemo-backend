# parties/urls.py
from rest_framework.routers import DefaultRouter

from .views import PartySubTypeMasterViewSet, PartyViewSet

router = DefaultRouter()
router.register("parties", PartyViewSet, basename="party")
router.register("party-sub-types", PartySubTypeMasterViewSet, basename="party-sub-type")

urlpatterns = router.urls
