# hsn/urls.py
from rest_framework.routers import DefaultRouter

from .views import HSNItemTypeViewSet, HSNProductCategoryViewSet, HSNViewSet

router = DefaultRouter()
router.register("hsn", HSNViewSet, basename="hsn")
router.register("hsn-item-types", HSNItemTypeViewSet, basename="hsn-item-type")
router.register("hsn-product-categories", HSNProductCategoryViewSet, basename="hsn-product-category")
urlpatterns = router.urls
