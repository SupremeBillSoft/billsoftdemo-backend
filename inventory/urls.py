# inventory/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import GodownViewSet, StockAdjustmentView, StockListView, StockTransferViewSet

router = DefaultRouter()
router.register("godowns", GodownViewSet, basename="godown")
router.register("inventory/transfers", StockTransferViewSet, basename="stock-transfer")

urlpatterns = [  # noqa: RUF005
    path("inventory/stock/", StockListView.as_view(), name="inventory-stock"),
    path("inventory/adjustments/", StockAdjustmentView.as_view(), name="inventory-adjustments"),
] + router.urls
