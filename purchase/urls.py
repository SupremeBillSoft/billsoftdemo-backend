# purchase/urls.py - CONSOLIDATED (one router, all five resources)

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DebitNoteViewSet,
    ItemGstOptionsView,
    PurchaseInvoiceViewSet,
    PurchaseOrderViewSet,
    PurchaseRequisitionViewSet,
    PurchaseReturnViewSet,
)

router = DefaultRouter()
router.register("purchase-requisitions", PurchaseRequisitionViewSet, basename="purchase-requisition")
router.register("purchase-orders", PurchaseOrderViewSet, basename="purchase-order")
router.register("purchase-invoices", PurchaseInvoiceViewSet, basename="purchase-invoice")
router.register("purchase-returns", PurchaseReturnViewSet, basename="purchase-return")
router.register("debit-notes", DebitNoteViewSet, basename="debit-note")

urlpatterns = [  # noqa: RUF005
    path("item-gst-options/", ItemGstOptionsView.as_view(), name="item-gst-options"),
] + router.urls
