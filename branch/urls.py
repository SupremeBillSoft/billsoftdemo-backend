# branch/urls.py
from rest_framework.routers import DefaultRouter

from .views import BranchTypeViewSet, BranchViewSet

router = DefaultRouter()
router.register("branches", BranchViewSet, basename="branch")
router.register("branch-types", BranchTypeViewSet, basename="branch-type")
urlpatterns = router.urls
