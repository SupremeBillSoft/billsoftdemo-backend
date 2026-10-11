# sales/urls.py - all under /api/sales/

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CreditNoteViewSet,
    CustomerItemsView,
    DeliveryChallanViewSet,
    EnquiryViewSet,
    EWayBillViewSet,
    QuotationViewSet,
    SaleOrderViewSet,
    SalesInvoiceViewSet,
    SalesReturnViewSet,
)

router = DefaultRouter()
router.register("enquiries", EnquiryViewSet, basename="enquiry")
router.register("quotations", QuotationViewSet, basename="quotation")
router.register("sale-orders", SaleOrderViewSet, basename="sale-order")
router.register("delivery-challans", DeliveryChallanViewSet, basename="delivery-challan")
router.register("invoices", SalesInvoiceViewSet, basename="sales-invoice")
router.register("returns", SalesReturnViewSet, basename="sales-return")
router.register("credit-notes", CreditNoteViewSet, basename="credit-note")
router.register("eway-bills", EWayBillViewSet, basename="eway-bill")

urlpatterns = [  # noqa: RUF005
    path("customer-items/", CustomerItemsView.as_view(), name="sales-customer-items"),
] + router.urls
