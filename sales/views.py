# sales/views.py

import datetime
from collections import defaultdict
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from parties.models import PartyItem
from tenants.services import current_user_name, get_current_tenant

from .compliance import actions as compliance
from .models import (
    CreditNote,
    DeliveryChallan,
    Enquiry,
    EnquiryFollowUp,
    EWayBill,
    Quotation,
    SaleOrder,
    SalesInvoice,
    SalesReturn,
)
from .serializers import (
    CancelSerializer,
    ChallanReturnSerializer,
    CreditNoteSerializer,
    CustomerItemSerializer,
    DeliveryChallanItemSerializer,
    DeliveryChallanSerializer,
    EnquiryFollowUpSerializer,
    EnquirySerializer,
    EWayBillRequestSerializer,
    EWayBillSerializer,
    QuotationSerializer,
    SaleOrderItemSerializer,
    SaleOrderRevisionSerializer,
    SaleOrderSerializer,
    SalesInvoiceItemSerializer,
    SalesInvoiceSerializer,
    SalesReturnSerializer,
    UpdateVehicleSerializer,
    refresh_challan_status,
)
from .services import (
    close_sale_order,
    post_challan_stock,
    post_invoice_stock,
    post_sales_return_stock,
    quotation_to_invoice_data,
    quotation_to_sale_order_data,
    refresh_sale_order_status,
    sale_order_has_fulfilment,
    sale_orders_to_invoice_data,
    short_close_lines,
)

ZERO = Decimal("0")


class TenantScopedMixin:
    model = None

    def tenant(self):
        return get_current_tenant(self.request)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = self.tenant()
        context["user_name"] = current_user_name(self.request)
        return context

    def filter_common(self, queryset, *, date_field: str, search_fields: list[str]):
        """BRD Search & Filter: ?from=&to= on the document date, ?status=,
        ?customer=, ?search= (number / customer name)."""
        params = self.request.query_params
        if params.get("from"):
            queryset = queryset.filter(**{f"{date_field}__gte": params["from"]})
        if params.get("to"):
            queryset = queryset.filter(**{f"{date_field}__lte": params["to"]})
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("customer"):
            queryset = queryset.filter(customer_id=params["customer"])
        if params.get("search"):
            q = Q()
            for field in search_fields:
                q |= Q(**{f"{field}__icontains": params["search"]})
            queryset = queryset.filter(q)
        return queryset

    def create_from(self, serializer_class, data):
        serializer = serializer_class(data=data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer


# ---------------------------------------------------------------------------
# Enquiry
# ---------------------------------------------------------------------------


class EnquiryViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    serializer_class = EnquirySerializer

    def get_queryset(self):
        qs = Enquiry.objects.filter(tenant=self.tenant()).prefetch_related("follow_ups")
        qs = self.filter_common(
            qs, date_field="enquiry_date", search_fields=["enquiry_number", "customer_name", "area"]
        )
        if self.request.query_params.get("area"):
            qs = qs.filter(area__icontains=self.request.query_params["area"])
        return qs

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        return Response(
            {"pendingEnquiries": Enquiry.objects.filter(tenant=self.tenant(), status=Enquiry.STATUS_OPEN).count()}
        )

    @action(detail=True, methods=["post"], url_path="follow-ups")
    def add_follow_up(self, request, pk=None):
        enquiry = self.get_object()
        if enquiry.status == Enquiry.STATUS_CLOSED:
            raise ValidationError("This enquiry is closed.")
        serializer = EnquiryFollowUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(enquiry=enquiry, created_by=current_user_name(request))
        return Response(
            EnquirySerializer(self.get_object(), context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["patch"], url_path=r"follow-ups/(?P<follow_up_id>\d+)")
    def update_follow_up(self, request, pk=None, follow_up_id=None):
        enquiry = self.get_object()
        follow_up = EnquiryFollowUp.objects.filter(enquiry=enquiry, pk=follow_up_id).first()
        if follow_up is None:
            raise ValidationError("Follow-up not found.")
        serializer = EnquiryFollowUpSerializer(follow_up, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(EnquirySerializer(self.get_object(), context=self.get_serializer_context()).data)


# ---------------------------------------------------------------------------
# Quotation
# ---------------------------------------------------------------------------


class QuotationViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    serializer_class = QuotationSerializer

    def get_queryset(self):
        qs = (
            Quotation.objects.filter(tenant=self.tenant())
            .select_related("customer", "enquiry")
            .prefetch_related("items__item", "items__tax", "charges")
        )
        return self.filter_common(
            qs,
            date_field="quotation_date",
            search_fields=["quotation_number", "customer__party_name", "customer__party_code"],
        )

    def perform_update(self, serializer):
        if serializer.instance.status in Quotation.LOCKED_STATUSES:
            raise ValidationError(f"A {serializer.instance.status} quotation can't be modified.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status == Quotation.STATUS_CONVERTED:
            raise ValidationError("A converted quotation can't be deleted.")
        instance.delete()

    def _set_status(self, quotation, new_status, allowed_from):
        if quotation.status not in allowed_from:
            raise ValidationError(f"A {quotation.status} quotation can't be marked {new_status}.")
        quotation.status = new_status
        quotation.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(quotation).data)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        today = datetime.date.today()
        open_quotes = Quotation.objects.filter(
            tenant=self.tenant(), status__in=[Quotation.STATUS_DRAFT, Quotation.STATUS_SENT]
        )
        return Response({"pendingQuotations": sum(1 for q in open_quotes if not q.is_expired(today))})

    @action(detail=True, methods=["post"], url_path="mark-sent")
    def mark_sent(self, request, pk=None):
        return self._set_status(self.get_object(), Quotation.STATUS_SENT, {Quotation.STATUS_DRAFT})

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        quotation = self.get_object()
        if quotation.is_expired(datetime.date.today()):
            raise ValidationError("This quotation has expired - revise Valid Till first.")
        return self._set_status(quotation, Quotation.STATUS_ACCEPTED, {Quotation.STATUS_DRAFT, Quotation.STATUS_SENT})

    @action(detail=True, methods=["post"], url_path="decline")
    def decline(self, request, pk=None):
        return self._set_status(
            self.get_object(), Quotation.STATUS_DECLINED, {Quotation.STATUS_DRAFT, Quotation.STATUS_SENT}
        )

    @action(detail=True, methods=["post"], url_path="convert-to-sales-order")
    def convert_to_sales_order(self, request, pk=None):
        quotation = self.get_object()
        if quotation.status != Quotation.STATUS_ACCEPTED:
            raise ValidationError("Only an accepted quotation can be converted.")
        with transaction.atomic():
            created = self.create_from(
                SaleOrderSerializer, quotation_to_sale_order_data(quotation, datetime.date.today())
            )
        return Response(created.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="convert-to-invoice")
    def convert_to_invoice(self, request, pk=None):
        """BRD 6.4.2 "Convert to Invoice" - for sales without an order
        stage. `godown` is where stocked items leave from."""
        quotation = self.get_object()
        if quotation.status != Quotation.STATUS_ACCEPTED:
            raise ValidationError("Only an accepted quotation can be converted.")
        data = quotation_to_invoice_data(quotation, datetime.date.today())
        for line in data["items"]:
            line["godown"] = request.data.get("godown")
        with transaction.atomic():
            created = self.create_from(SalesInvoiceSerializer, data)
        return Response(created.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Sale Order
# ---------------------------------------------------------------------------


class SaleOrderViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    serializer_class = SaleOrderSerializer

    def get_queryset(self):
        qs = (
            SaleOrder.objects.filter(tenant=self.tenant())
            .select_related("customer", "quotation")
            .prefetch_related("items__item", "items__tax", "charges")
        )
        qs = self.filter_common(
            qs, date_field="so_date", search_fields=["so_number", "customer__party_name", "customer__party_code"]
        )
        if self.request.query_params.get("so_type"):
            qs = qs.filter(so_type=self.request.query_params["so_type"])
        return qs

    def perform_update(self, serializer):
        if serializer.instance.status == SaleOrder.STATUS_CLOSED:
            raise ValidationError("A closed order can't be amended.")
        serializer.save()

    def perform_destroy(self, instance):
        if sale_order_has_fulfilment(instance):
            raise ValidationError("This order has deliveries or invoices - close it instead of deleting.")
        instance.delete()

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        qs = SaleOrder.objects.filter(tenant=self.tenant())
        return Response(
            {"totalSaleOrders": qs.count(), "pendingSaleOrders": qs.filter(status=SaleOrder.STATUS_PENDING).count()}
        )

    @action(detail=True, methods=["get"], url_path="revisions")
    def revisions(self, request, pk=None):
        return Response(SaleOrderRevisionSerializer(self.get_object().revisions.all(), many=True).data)

    @action(detail=True, methods=["get"], url_path="pending-lines")
    def pending_lines(self, request, pk=None):
        lines = [line for line in self.get_object().items.all() if line.pending_qty > ZERO]
        return Response(SaleOrderItemSerializer(lines, many=True).data)

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        order = self.get_object()
        close_sale_order(order, request.data.get("reason", ""))
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="close-lines")
    def close_lines(self, request, pk=None):
        order = self.get_object()
        if order.status == SaleOrder.STATUS_CLOSED:
            raise ValidationError("This order is already closed.")
        short_close_lines(order, [int(i) for i in request.data.get("line_ids", [])])
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=False, methods=["post"], url_path="bulk-close")
    def bulk_close(self, request):
        closed, failed = [], []
        for order in self.get_queryset().filter(id__in=request.data.get("ids", [])):
            try:
                close_sale_order(order, request.data.get("reason", ""))
                closed.append(order.id)
            except ValidationError as exc:
                failed.append({"id": order.id, "error": str(exc.detail)})
        return Response({"closed": closed, "failed": failed})

    @action(detail=True, methods=["post"], url_path="convert-to-invoice")
    def convert_to_invoice(self, request, pk=None):
        order = self.get_object()
        if order.status == SaleOrder.STATUS_CLOSED:
            raise ValidationError("This order is closed.")
        with transaction.atomic():
            created = self.create_from(
                SalesInvoiceSerializer,
                sale_orders_to_invoice_data([order], datetime.date.today(), request.data.get("godown")),
            )
        return Response(created.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="bulk-convert-to-invoice")
    def bulk_convert_to_invoice(self, request):
        """BRD "convert selected sale orders into invoices in bulk (customer
        based)": one invoice per customer; failures reported per customer
        without stopping the others."""
        orders = list(
            self.get_queryset().filter(id__in=request.data.get("ids", [])).exclude(status=SaleOrder.STATUS_CLOSED)
        )
        by_customer = defaultdict(list)
        for order in orders:
            by_customer[(order.customer_id, order.so_type)].append(order)
        created, failed = [], []
        for (customer_id, _), group in by_customer.items():
            try:
                with transaction.atomic():
                    serializer = self.create_from(
                        SalesInvoiceSerializer,
                        sale_orders_to_invoice_data(group, datetime.date.today(), request.data.get("godown")),
                    )
                created.append(serializer.data["invoice_number"])
            except ValidationError as exc:
                failed.append({"customer": customer_id, "orders": [o.so_number for o in group], "error": exc.detail})
        return Response(
            {"invoices": created, "failed": failed},
            status=status.HTTP_201_CREATED if created else status.HTTP_400_BAD_REQUEST,
        )


# ---------------------------------------------------------------------------
# Delivery Challan
# ---------------------------------------------------------------------------


class DeliveryChallanViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    serializer_class = DeliveryChallanSerializer

    def get_queryset(self):
        qs = (
            DeliveryChallan.objects.filter(tenant=self.tenant())
            .select_related("customer", "sale_order")
            .prefetch_related("items__item", "items__godown")
        )
        qs = self.filter_common(
            qs, date_field="dc_date", search_fields=["dc_number", "customer__party_name", "customer__party_code"]
        )
        if self.request.query_params.get("dc_type"):
            qs = qs.filter(dc_type=self.request.query_params["dc_type"])
        return qs

    def _has_downstream(self, challan) -> bool:
        return challan.invoices.exclude(status=SalesInvoice.STATUS_CANCELLED).exists() or challan.returns.exists()

    def perform_update(self, serializer):
        challan = serializer.instance
        if challan.status == DeliveryChallan.STATUS_CANCELLED:
            raise ValidationError("A cancelled challan can't be modified.")
        if self._has_downstream(challan):
            raise ValidationError("This challan has been invoiced or received back and can't be modified.")
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, instance):
        if self._has_downstream(instance) or instance.invoices.exists():
            raise ValidationError("This challan has been invoiced or received back and can't be deleted.")
        post_challan_stock(instance, current_user_name(self.request), remove=True)
        order = instance.sale_order
        instance.delete()
        if order:
            refresh_sale_order_status(order)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        qs = DeliveryChallan.objects.filter(tenant=self.tenant(), status=DeliveryChallan.STATUS_OPEN)
        overdue = qs.filter(returnable=True, return_due_date__lt=datetime.date.today()).count()
        return Response({"pendingChallans": qs.count(), "overdueReturnables": overdue})

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        challan = self.get_object()
        if challan.status == DeliveryChallan.STATUS_CANCELLED:
            raise ValidationError("Already cancelled.")
        if self._has_downstream(challan):
            raise ValidationError("This challan has been invoiced or received back and can't be cancelled.")
        with transaction.atomic():
            challan.status = DeliveryChallan.STATUS_CANCELLED
            challan.cancel_reason = request.data.get("reason", "")
            challan.save(update_fields=["status", "cancel_reason", "updated_at"])
            post_challan_stock(challan, current_user_name(request), remove=True)
            if challan.sale_order_id:
                refresh_sale_order_status(challan.sale_order)
        return Response(self.get_serializer(challan).data)

    @action(detail=True, methods=["post"], url_path="receive-back")
    def receive_back(self, request, pk=None):
        challan = self.get_object()
        context = {**self.get_serializer_context(), "delivery_challan": challan}
        serializer = ChallanReturnSerializer(data=request.data, context=context)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self.get_serializer(self.get_object()).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="invoiceable-lines")
    def invoiceable_lines(self, request):
        """Billable challan lines still to be invoiced, for the DC-based
        invoice picker (?customer=)."""
        qs = (
            self.get_queryset()
            .filter(status=DeliveryChallan.STATUS_OPEN, returnable=False)
            .exclude(dc_type=DeliveryChallan.TYPE_NON_BILLABLE)
        )
        rows = []
        for challan in qs:
            lines = [line for line in challan.items.all() if line.pending_invoice_qty > ZERO]
            if lines:
                rows.append(
                    {
                        "id": challan.id,
                        "dcNumber": challan.dc_number,
                        "dcDate": challan.dc_date,
                        "lines": DeliveryChallanItemSerializer(lines, many=True).data,
                    }
                )
        return Response(rows)

    @action(detail=False, methods=["get"], url_path="overdue-returnables")
    def overdue_returnables(self, request):
        """Job-work / returnable challans past their s.143 return date: the
        unreturned quantity becomes a taxable supply."""
        qs = (
            self.get_queryset()
            .filter(returnable=True, return_due_date__lt=datetime.date.today())
            .exclude(status__in=[DeliveryChallan.STATUS_CLOSED, DeliveryChallan.STATUS_CANCELLED])
        )
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="eway-bill-check")
    def eway_bill_check(self, request, pk=None):
        return Response(compliance.eway_bill_check(self.get_object()))

    @action(detail=True, methods=["post"], url_path="eway-bill")
    def eway_bill(self, request, pk=None):
        payload = EWayBillRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ewb = compliance.generate_eway_bill(self.get_object(), payload.validated_data, current_user_name(request))
        return Response(EWayBillSerializer(ewb).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Sales Invoice
# ---------------------------------------------------------------------------


def _refresh_links(invoice: SalesInvoice) -> None:
    orders = {
        line.so_item.sale_order for line in invoice.items.select_related("so_item__sale_order") if line.so_item_id
    }
    for order in orders:
        refresh_sale_order_status(order)
    for challan in invoice.delivery_challans.all():
        refresh_challan_status(challan)


class SalesInvoiceViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    serializer_class = SalesInvoiceSerializer

    def get_queryset(self):
        qs = (
            SalesInvoice.objects.filter(tenant=self.tenant())
            .select_related("customer", "sale_order")
            .prefetch_related("items__item", "items__tax", "items__godown", "charges", "delivery_challans")
        )
        qs = self.filter_common(
            qs,
            date_field="invoice_date",
            search_fields=["invoice_number", "customer__party_name", "customer__party_code", "irn"],
        )
        params = self.request.query_params
        if params.get("invoice_type"):
            qs = qs.filter(invoice_type=params["invoice_type"])
        if params.get("tax_nature"):
            qs = qs.filter(tax_nature=params["tax_nature"])
        if params.get("einvoice_status"):
            qs = qs.filter(einvoice_status=params["einvoice_status"])
        return qs

    def _locked_reason(self, invoice) -> str | None:
        if invoice.status == SalesInvoice.STATUS_CANCELLED:
            return "A cancelled invoice can't be modified."
        if invoice.einvoice_status == SalesInvoice.EINV_GENERATED:
            return "An e-invoiced document can't be edited - cancel the IRN (within 24 h) or issue a Credit Note."
        if (
            invoice.sales_returns.exclude(status=SalesReturn.STATUS_CANCELLED).exists()
            or invoice.credit_notes.exclude(status=CreditNote.STATUS_CANCELLED).exists()
        ):
            return "This invoice has returns or credit notes against it - correct it with a Credit Note."
        return None

    def perform_update(self, serializer):
        reason = self._locked_reason(serializer.instance)
        if reason:
            raise ValidationError(reason)
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, instance):
        if instance.irn or (self._locked_reason(instance) and instance.status != SalesInvoice.STATUS_CANCELLED):
            raise ValidationError(
                "Only a draft-like invoice (never e-invoiced, nothing against it) can be deleted - cancel it instead."
            )
        post_invoice_stock(instance, current_user_name(self.request), remove=True)
        challans = list(instance.delivery_challans.all())
        orders = {line.so_item.sale_order for line in instance.items.all() if line.so_item_id}
        instance.delete()
        for order in orders:
            refresh_sale_order_status(order)
        for challan in challans:
            refresh_challan_status(challan)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        active = SalesInvoice.objects.filter(tenant=self.tenant(), status=SalesInvoice.STATUS_ACTIVE).exclude(
            invoice_type=SalesInvoice.TYPE_PROFORMA
        )
        unpaid = [inv for inv in active if inv.balance_amount > ZERO]
        return Response(
            {
                "pendingInvoices": len(unpaid),
                "receivable": str(sum((inv.balance_amount for inv in unpaid), ZERO)),
                "einvoicePending": active.filter(einvoice_status=SalesInvoice.EINV_PENDING).count(),
            }
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        invoice = self.get_object()
        if invoice.status == SalesInvoice.STATUS_CANCELLED:
            raise ValidationError("Already cancelled.")
        if (
            invoice.sales_returns.exclude(status=SalesReturn.STATUS_CANCELLED).exists()
            or invoice.credit_notes.exclude(status=CreditNote.STATUS_CANCELLED).exists()
        ):
            raise ValidationError("Cancel its returns / credit notes first.")
        if invoice.einvoice_status == SalesInvoice.EINV_GENERATED:
            raise ValidationError("Cancel the IRN first (allowed within 24 hours); after that, issue a Credit Note.")
        with transaction.atomic():
            invoice.status = SalesInvoice.STATUS_CANCELLED
            invoice.cancel_reason = request.data.get("reason", "")
            invoice.cancelled_at = timezone.now()
            invoice.save(update_fields=["status", "cancel_reason", "cancelled_at", "updated_at"])
            post_invoice_stock(invoice, current_user_name(request), remove=True)
            _refresh_links(invoice)
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["get"], url_path="returnable-lines")
    def returnable_lines(self, request, pk=None):
        lines = [line for line in self.get_object().items.all() if line.returnable_qty > ZERO]
        return Response(SalesInvoiceItemSerializer(lines, many=True).data)

    @action(detail=True, methods=["post"], url_path="einvoice/generate")
    def einvoice_generate(self, request, pk=None):
        invoice = self.get_object()
        compliance.generate_einvoice(invoice)
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="einvoice/cancel")
    def einvoice_cancel(self, request, pk=None):
        payload = CancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        invoice = self.get_object()
        compliance.cancel_einvoice(invoice, **payload.validated_data)
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["get"], url_path="eway-bill-check")
    def eway_bill_check(self, request, pk=None):
        return Response(compliance.eway_bill_check(self.get_object()))

    @action(detail=True, methods=["post"], url_path="eway-bill")
    def eway_bill(self, request, pk=None):
        payload = EWayBillRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ewb = compliance.generate_eway_bill(self.get_object(), payload.validated_data, current_user_name(request))
        return Response(EWayBillSerializer(ewb).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Sales Return and Credit Note
# ---------------------------------------------------------------------------


class SalesReturnViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    serializer_class = SalesReturnSerializer

    def get_queryset(self):
        qs = (
            SalesReturn.objects.filter(tenant=self.tenant())
            .select_related("customer", "sales_invoice")
            .prefetch_related("items__item", "items__tax", "items__godown", "charges")
        )
        qs = self.filter_common(
            qs,
            date_field="return_date",
            search_fields=["return_number", "sales_invoice__invoice_number", "customer__party_name"],
        )
        if self.request.query_params.get("sales_invoice"):
            qs = qs.filter(sales_invoice_id=self.request.query_params["sales_invoice"])
        return qs

    def perform_update(self, serializer):
        ret = serializer.instance
        if ret.status == SalesReturn.STATUS_CANCELLED:
            raise ValidationError("A cancelled return can't be modified.")
        if ret.credit_notes.exclude(status=CreditNote.STATUS_CANCELLED).exists():
            raise ValidationError("A credit note has been issued for this return - it can't be modified.")
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, instance):
        if instance.credit_notes.exists():
            raise ValidationError("A credit note references this return - cancel it instead.")
        post_sales_return_stock(instance, current_user_name(self.request), remove=True)
        instance.delete()

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        ret = self.get_object()
        if ret.status == SalesReturn.STATUS_CANCELLED:
            raise ValidationError("Already cancelled.")
        if ret.credit_notes.exclude(status=CreditNote.STATUS_CANCELLED).exists():
            raise ValidationError("Cancel the credit note for this return first.")
        with transaction.atomic():
            ret.status = SalesReturn.STATUS_CANCELLED
            ret.cancel_reason = request.data.get("reason", "")
            ret.save(update_fields=["status", "cancel_reason", "updated_at"])
            post_sales_return_stock(ret, current_user_name(request), remove=True)
        return Response(self.get_serializer(ret).data)


class CreditNoteViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    serializer_class = CreditNoteSerializer

    def get_queryset(self):
        qs = (
            CreditNote.objects.filter(tenant=self.tenant())
            .select_related("customer", "sales_invoice", "sales_return")
            .prefetch_related("items__item", "items__tax", "charges")
        )
        qs = self.filter_common(
            qs,
            date_field="credit_note_date",
            search_fields=[
                "credit_note_number",
                "sales_invoice__invoice_number",
                "customer__party_name",
                "customer__party_code",
                "irn",
            ],
        )
        params = self.request.query_params
        if params.get("sales_invoice"):
            qs = qs.filter(sales_invoice_id=params["sales_invoice"])
        if params.get("einvoice_status"):
            qs = qs.filter(einvoice_status=params["einvoice_status"])
        return qs

    def perform_update(self, serializer):
        note = serializer.instance
        if note.status == CreditNote.STATUS_CANCELLED:
            raise ValidationError("A cancelled credit note can't be modified.")
        if note.einvoice_status == CreditNote.EINV_GENERATED:
            raise ValidationError("An e-invoiced credit note can't be edited - cancel its IRN (within 24 h) first.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.irn:
            raise ValidationError("An e-invoiced credit note can't be deleted - cancel it instead.")
        instance.delete()

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        note = self.get_object()
        if note.status == CreditNote.STATUS_CANCELLED:
            raise ValidationError("Already cancelled.")
        if note.einvoice_status == CreditNote.EINV_GENERATED:
            raise ValidationError("Cancel the IRN first (allowed within 24 hours).")
        note.status = CreditNote.STATUS_CANCELLED
        note.cancel_reason = request.data.get("reason", "")
        note.save(update_fields=["status", "cancel_reason", "updated_at"])
        return Response(self.get_serializer(note).data)

    @action(detail=True, methods=["post"], url_path="einvoice/generate")
    def einvoice_generate(self, request, pk=None):
        note = self.get_object()
        compliance.generate_einvoice(note)
        return Response(self.get_serializer(note).data)

    @action(detail=True, methods=["post"], url_path="einvoice/cancel")
    def einvoice_cancel(self, request, pk=None):
        payload = CancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        note = self.get_object()
        compliance.cancel_einvoice(note, **payload.validated_data)
        return Response(self.get_serializer(note).data)


# ---------------------------------------------------------------------------
# E-Way Bill list + Part-B / cancel, and customer part codes
# ---------------------------------------------------------------------------


class EWayBillViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = EWayBillSerializer

    def get_queryset(self):
        qs = EWayBill.objects.filter(tenant=self.tenant()).select_related("content_type")
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("from"):
            qs = qs.filter(ewb_date__date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(ewb_date__date__lte=params["to"])
        if params.get("document_type") in ("SalesInvoice", "DeliveryChallan"):
            model = SalesInvoice if params["document_type"] == "SalesInvoice" else DeliveryChallan
            qs = qs.filter(content_type=ContentType.objects.get_for_model(model))
        if params.get("search"):
            q = params["search"]
            qs = qs.filter(
                Q(ewb_number__icontains=q)
                | Q(document_number__icontains=q)
                | Q(vehicle_no__icontains=q)
                | Q(transporter_name__icontains=q)
            )
        return qs

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        payload = CancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ewb = self.get_object()
        compliance.cancel_eway_bill(ewb, **payload.validated_data)
        return Response(self.get_serializer(ewb).data)

    @action(detail=True, methods=["post"], url_path="update-vehicle")
    def update_vehicle(self, request, pk=None):
        payload = UpdateVehicleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ewb = self.get_object()
        compliance.update_vehicle(ewb, **payload.validated_data)
        return Response(self.get_serializer(ewb).data)


class CustomerItemsView(APIView):
    """GET /api/sales/customer-items/?customer=<id> - legacy "Part Code
    Load": the items mapped to this customer in Party Master, with the part
    code printed on their documents."""

    def get(self, request):
        tenant = get_current_tenant(request)
        rows = PartyItem.objects.filter(
            party__tenant=tenant, party_id=request.query_params.get("customer")
        ).select_related("item")
        return Response(CustomerItemSerializer(rows, many=True).data)
