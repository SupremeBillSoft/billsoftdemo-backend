# units/urls.py

from rest_framework.routers import DefaultRouter

from .views import UnitViewSet

router = DefaultRouter()
router.register("units", UnitViewSet, basename="unit")
urlpatterns = router.urls
