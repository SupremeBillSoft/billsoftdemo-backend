# purchase/views.py - CONSOLIDATED (PR + PO + Purchase Invoice + Purchase
# Return + Debit Note, in dependency order).

from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.mixins import ApprovalActionMixin
from items.models import Item
from tenants.services import current_user_name, current_user_role, get_current_tenant

from .gst_resolution import resolve_item_default_tax, resolve_seller_state
from .models import (
    DebitNote,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseRequisition,
    PurchaseReturn,
    PurchaseReturnItem,
)
from .serializers import (
    ApprovedPOForInvoiceSerializer,
    DebitNoteSerializer,
    InvoiceForReturnSerializer,
    OrderablePRSerializer,
    PurchaseInvoiceItemSerializer,
    PurchaseInvoiceSerializer,
    PurchaseOrderItemSerializer,
    PurchaseOrderSerializer,
    PurchaseRequisitionSerializer,
    PurchaseReturnSerializer,
    apply_po_deltas,
    post_invoice_stock,
    post_return_stock,
)

# ---------------------------------------------------------------------------
# Live GST preview support (frontend item-entry grids, before save)
# ---------------------------------------------------------------------------


class ItemGstOptionsView(APIView):
    """GET /item-gst-options/ - for every active Item, its DEFAULT Tax
    master id (item's own tax_type, else its HSN's - see
    gst_resolution.resolve_item_default_tax), plus the company's state from
    Company Settings. The item-entry grids pre-select that tax on each line
    (the user can change it) and compute a LIVE preview from the Tax master
    rows (lib/gst/taxRates.ts) - the actual save is still recalculated
    server-side."""

    def get(self, request):
        tenant = get_current_tenant(request)
        items = Item.objects.filter(tenant=tenant, item_status="Active")
        item_taxes = []
        for item in items:
            tax = resolve_item_default_tax(item, tenant)
            item_taxes.append({"id": item.id, "taxId": tax.id if tax else None})
        return Response({"sellerState": resolve_seller_state(tenant), "items": item_taxes})


# ---------------------------------------------------------------------------
# Purchase Requisition
# ---------------------------------------------------------------------------


class PurchaseRequisitionViewSet(ApprovalActionMixin, viewsets.ModelViewSet):
    serializer_class = PurchaseRequisitionSerializer

    def get_queryset(self):
        queryset = (
            PurchaseRequisition.objects.filter(tenant=get_current_tenant(self.request))
            .select_related("supplier")
            .prefetch_related("items", "items__item", "items__po_lines__purchase_order")
        )
        # Optional ?status=Pending - backs the PR Approval page's "only
        # items awaiting approval" list server-side instead of fetching
        # every PR and filtering in the browser. Only applied on list so
        # detail/decision lookups by id still work for any status.
        status_filter = self.request.query_params.get("status")
        if self.action == "list" and status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = get_current_tenant(self.request)
        context["user_role"] = current_user_role(self.request)
        return context

    def perform_update(self, serializer):
        # Row-level actions on the PR list are status-dependent - Modify
        # only makes sense while still Pending.
        if serializer.instance.status != PurchaseRequisition.STATUS_PENDING:
            raise ValidationError("Only a Pending PR can be modified.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != PurchaseRequisition.STATUS_PENDING:
            raise ValidationError("Only a Pending PR can be deleted.")
        instance.delete()


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------


class PurchaseOrderViewSet(ApprovalActionMixin, viewsets.ModelViewSet):
    serializer_class = PurchaseOrderSerializer

    def get_queryset(self):
        queryset = (
            PurchaseOrder.objects.filter(tenant=get_current_tenant(self.request))
            .select_related("supplier")
            .prefetch_related("items", "items__item", "items__pr_item__purchase_requisition", "items__tax")
        )
        # Optional ?status=Pending - backs the PO Approval page, same as PR's.
        status_filter = self.request.query_params.get("status")
        if self.action == "list" and status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = get_current_tenant(self.request)
        context["user_role"] = current_user_role(self.request)
        return context

    def perform_update(self, serializer):
        if serializer.instance.status != PurchaseOrder.STATUS_PENDING:
            raise ValidationError("Only a Pending PO can be modified.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != PurchaseOrder.STATUS_PENDING:
            raise ValidationError("Only a Pending PO can be deleted.")
        # purchase_order on PurchaseInvoice is on_delete=PROTECT, so this
        # would 500 with a raw ProtectedError without this check.
        if instance.purchase_invoices.exists():
            raise ValidationError("This Purchase Order already has one or more invoices " \
            "against it and cannot be deleted.")
        instance.delete()

    @action(detail=False, methods=["get"], url_path="approved-requisitions")
    def approved_requisitions(self, request):
        """Backs the PR picker on the Create PO screen (BRD 6.5.2.1.2 - "list
        of PR No.s based on the Party Code selected"). Returns Approved PRs
        that still have quantity left to order, each with its open lines.
        ?supplier=<id> narrows to that supplier's PRs plus PRs raised without
        a supplier."""
        tenant = get_current_tenant(request)
        queryset = PurchaseRequisition.objects.filter(
            tenant=tenant, status=PurchaseRequisition.STATUS_APPROVED
        ).prefetch_related("items__item", "items__po_lines__purchase_order")
        supplier_id = request.query_params.get("supplier")
        if supplier_id:
            queryset = queryset.filter(Q(supplier_id=supplier_id) | Q(supplier__isnull=True))
        eligible = [pr for pr in queryset if any(line.pending_order_qty > Decimal("0") for line in pr.items.all())]
        return Response(OrderablePRSerializer(eligible, many=True).data)

    @action(detail=True, methods=["get"], url_path="pending-lines")
    def pending_lines(self, request, pk=None):
        """Returns this PO's lines that still have pending_qty > 0, for the
        Create Purchase Invoice screen's item grid - the frontend caps each
        row's Invoiced Qty input against this, and the PI serializer's
        `validate()` is still the authoritative check server-side."""
        po = self.get_object()
        lines = [line for line in po.items.all() if line.pending_qty > Decimal("0")]
        return Response(PurchaseOrderItemSerializer(lines, many=True).data)


# ---------------------------------------------------------------------------
# Purchase Invoice
# ---------------------------------------------------------------------------


class PurchaseInvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseInvoiceSerializer

    def get_queryset(self):
        return (
            PurchaseInvoice.objects.filter(tenant=get_current_tenant(self.request))
            .select_related("supplier", "purchase_order")
            .prefetch_related("items", "items__item", "items__po_item", "items__tax", "items__godown")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = get_current_tenant(self.request)
        context["user_name"] = current_user_name(self.request)
        return context

    def _has_downstream_returns(self, instance) -> bool:
        return PurchaseReturnItem.objects.filter(invoice_item__purchase_invoice=instance).exists()

    def _has_debit_notes(self, instance, active_only: bool = False) -> bool:
        notes = instance.debit_notes.all()
        if active_only:
            notes = notes.exclude(status=DebitNote.STATUS_CANCELLED)
        return notes.exists()

    def perform_update(self, serializer):
        if serializer.instance.status != PurchaseInvoice.STATUS_ACTIVE:
            raise ValidationError("A Cancelled invoice cannot be modified.")
        if self._has_downstream_returns(serializer.instance):
            raise ValidationError("This invoice already has a Purchase Return against it and cannot be modified.")
        if self._has_debit_notes(serializer.instance, active_only=True):
            raise ValidationError("This invoice already has a Debit Note against it and cannot be modified.")
        serializer.save()

    def perform_destroy(self, instance):
        if self._has_downstream_returns(instance):
            raise ValidationError("This invoice already has a Purchase Return against it and cannot be deleted.")
        # debit_notes.purchase_invoice is on_delete=PROTECT - even a
        # Cancelled note keeps its link for audit.
        if self._has_debit_notes(instance):
            raise ValidationError("This invoice has Debit Notes linked to it and cannot be deleted.")
        with transaction.atomic():
            # A Cancelled invoice already gave back its PO qty and stock on
            # cancel - only an Active one still holds them.
            if instance.status == PurchaseInvoice.STATUS_ACTIVE:
                apply_po_deltas(list(instance.items.all()), sign=-1)
                post_invoice_stock(instance, current_user_name(self.request), remove=True)
            instance.delete()

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """The normal teardown path: keeps the record for audit, but
        reverses the pending_qty it consumed on the PO so those units can be
        invoiced again (e.g. a booking error, not a physical return of
        goods)."""
        invoice = self.get_object()
        if invoice.status == PurchaseInvoice.STATUS_CANCELLED:
            raise ValidationError("This invoice is already cancelled.")
        if self._has_downstream_returns(invoice):
            raise ValidationError("This invoice already has a Purchase Return against it and cannot be cancelled.")
        if self._has_debit_notes(invoice, active_only=True):
            raise ValidationError("This invoice has an active Debit Note against it and cannot be cancelled.")

        with transaction.atomic():
            apply_po_deltas(list(invoice.items.all()), sign=-1)
            # Refused if the received stock has already moved on (e.g.
            # transferred out) - see inventory.services.replace_document_movements.
            post_invoice_stock(invoice, current_user_name(request), remove=True)
            invoice.status = PurchaseInvoice.STATUS_CANCELLED
            invoice.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(invoice).data)

    @action(detail=False, methods=["get"], url_path="invoiceable-orders")
    def invoiceable_orders(self, request):
        """Backs the "create Invoice from Approved PO" picker - only
        Approved POs with at least one line still pending are offered."""
        tenant = get_current_tenant(request)
        queryset = (
            PurchaseOrder.objects.filter(tenant=tenant, status=PurchaseOrder.STATUS_APPROVED)
            .prefetch_related("items")
        )
        eligible = [po for po in queryset if any(line.pending_qty > Decimal("0") for line in po.items.all())]
        return Response(ApprovedPOForInvoiceSerializer(eligible, many=True).data)

    @action(detail=True, methods=["get"], url_path="returnable-lines")
    def returnable_lines(self, request, pk=None):
        """Returns this invoice's lines that still have returnable_qty > 0,
        for the Create Purchase Return screen's item grid - same pattern as
        PurchaseOrderViewSet.pending_lines for the Invoice screen."""
        invoice = self.get_object()
        lines = [line for line in invoice.items.all() if line.returnable_qty > Decimal("0")]
        return Response(PurchaseInvoiceItemSerializer(lines, many=True).data)


# ---------------------------------------------------------------------------
# Purchase Return
#
# No approval workflow (BRD 6.5.4): status is the refund status, derived
# from amount_refunded on every save. Row actions are Add/Modify/Delete/Print.
# ---------------------------------------------------------------------------


class PurchaseReturnViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseReturnSerializer

    def get_queryset(self):
        return (
            PurchaseReturn.objects.filter(tenant=get_current_tenant(self.request))
            .select_related("supplier", "purchase_invoice")
            .prefetch_related("items", "items__item", "items__invoice_item")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = get_current_tenant(self.request)
        context["user_name"] = current_user_name(self.request)
        return context

    @transaction.atomic
    def perform_destroy(self, instance):
        # Deleting a return puts its goods back into stock.
        post_return_stock(instance, current_user_name(self.request), remove=True)
        instance.delete()

    @action(detail=False, methods=["get"], url_path="returnable-invoices")
    def returnable_invoices(self, request):
        """Backs the "create Return from Purchase Invoice" picker - only
        Active invoices with at least one line still returnable are offered."""
        tenant = get_current_tenant(request)
        queryset = PurchaseInvoice.objects.filter(
            tenant=tenant, status=PurchaseInvoice.STATUS_ACTIVE
        ).prefetch_related("items")
        eligible = [inv for inv in queryset if any(line.returnable_qty > Decimal("0") for line in inv.items.all())]
        return Response(InvoiceForReturnSerializer(eligible, many=True).data)


# ---------------------------------------------------------------------------
# Debit Note
#
# Linked to a Purchase Invoice (or standalone). No approval workflow:
# status is the refund status, plus Cancelled via the cancel action.
# ---------------------------------------------------------------------------


class DebitNoteViewSet(viewsets.ModelViewSet):
    serializer_class = DebitNoteSerializer

    def get_queryset(self):
        return (
            DebitNote.objects.filter(tenant=get_current_tenant(self.request))
            .select_related("supplier", "purchase_invoice")
            .prefetch_related("items", "items__item")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = get_current_tenant(self.request)
        return context

    def perform_update(self, serializer):
        if serializer.instance.status == DebitNote.STATUS_CANCELLED:
            raise ValidationError("A Cancelled Debit Note cannot be modified.")
        serializer.save()

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Keeps the record for audit but takes it out of play - the BRD's
        "Cancel" row action, distinct from Delete."""
        debit_note = self.get_object()
        if debit_note.status == DebitNote.STATUS_CANCELLED:
            raise ValidationError("This Debit Note is already cancelled.")
        debit_note.status = DebitNote.STATUS_CANCELLED
        debit_note.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(debit_note).data)

    @action(detail=False, methods=["get"], url_path="linkable-invoices")
    def linkable_invoices(self, request):
        """Backs the "Link to Invoice (search and select)" picker -
        Active Purchase Invoices, optionally narrowed by ?supplier=<id>."""
        tenant = get_current_tenant(request)
        queryset = PurchaseInvoice.objects.filter(tenant=tenant, status=PurchaseInvoice.STATUS_ACTIVE)
        supplier_id = request.query_params.get("supplier")
        if supplier_id:
            queryset = queryset.filter(supplier_id=supplier_id)
        return Response(InvoiceForReturnSerializer(queryset, many=True).data)
