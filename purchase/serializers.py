# purchase/serializers.py - CONSOLIDATED (PR + PO + Purchase Invoice +
# Purchase Return + Debit Note, in dependency order).

from dataclasses import asdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from rest_framework import serializers

from approvals.models import ApprovalHistory
from approvals.services import record_decision
from common.gst.engine import compute_line_tax
from docnumbering.services import next_number
from inventory.models import Godown
from inventory.services import StockLine, replace_document_movements, tracks_stock
from parties.models import Party  # adjust if Party's status constant/field differs
from tax.models import Tax
from tenants.services import get_company_settings

from .gst_resolution import resolve_item_default_tax, resolve_line_gst_inputs
from .models import (
    DebitNote,
    DebitNoteItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequisition,
    PurchaseRequisitionItem,
    PurchaseReturn,
    PurchaseReturnItem,
)

ZERO = Decimal("0")

# ---------------------------------------------------------------------------
# Shared rules
# ---------------------------------------------------------------------------

# BRD 6.2.2 Party Screen Approval: "prevent inactive, rejected, or pending
# parties from being selected in business transactions". Party approval
# isn't built yet (every party's approval_status is still False), so only
# the status values that exist today are blocked.
# TODO: also block parties whose approval is pending/rejected once the
# Party approval screen exists.
BLOCKED_PARTY_STATUSES = {"Inactive", "Suspend/Hold"}


def check_supplier(serializer, party: Party | None, doc_label: str) -> Party | None:
    """Applied when a document is created or its supplier is changed - an
    existing document isn't blocked from being edited because its supplier
    was later suspended."""
    current = getattr(serializer.instance, "supplier", None)
    if party is not None and (current is None or current.pk != party.pk):
        if party.status in BLOCKED_PARTY_STATUSES:
            raise serializers.ValidationError(
                f"Supplier '{party.party_name}' is {party.status} and cannot be selected for a new {doc_label}."
            )
    return party


def check_tax(tax: Tax | None, tenant) -> None:
    if tax is None:
        return
    if tax.tenant_id != tenant.pk:
        raise serializers.ValidationError({"items": "Selected tax was not found."})
    if tax.status != "Active":
        raise serializers.ValidationError({"items": f"Tax '{tax.tax_name}' is Inactive and cannot be used."})


def check_godown(godown: Godown | None, tenant) -> None:
    if godown is not None and (godown.tenant_id != tenant.pk or godown.status != "Active"):
        raise serializers.ValidationError({"items": f"Godown '{godown}' is not available."})


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def price_line(tenant, supplier, item_data: dict, qty_field: str, *, default_tax: Tax | None = None,
               with_charges: bool = False) -> dict:
    """The one place a purchase line's money is worked out.

    Tax: the line's chosen Tax master row (BRD "Tax Type", user-changeable),
    else `default_tax` (e.g. the source PO/invoice line's tax), else the
    item's own default. The chosen tax is stored on the line.

    Taxable value: qty x rate, less discount%, plus P&F and freight - all
    taxed at the line's rate (agreed rule for freight/P&F billed with goods).
    """
    item = item_data["item"]
    tax = item_data.get("tax") or default_tax or resolve_item_default_tax(item, tenant)
    check_tax(tax, tenant)

    taxable_value = Decimal(item_data[qty_field]) * Decimal(item_data["rate"])
    extra = {}
    if with_charges:
        discount_amount = _money(taxable_value * Decimal(item_data.get("discount_percent") or 0) / 100)
        taxable_value = (
            taxable_value
            - discount_amount
            + Decimal(item_data.get("pack_forward_amount") or 0)
            + Decimal(item_data.get("freight_amount") or 0)
        )
        extra["discount_amount"] = discount_amount

    gst_inputs = resolve_line_gst_inputs(tax, supplier, tenant)
    result = compute_line_tax(taxable_value=taxable_value, **gst_inputs)
    return {**item_data, **extra, **asdict(result), "tax": tax}


def auto_approve_for_role(document, auto_approve_roles: list[str], creator_role: str | None,
                          doc_label: str) -> None:
    """BRD 6.5.1 / 6.5.2 auto approval, role-based (confirmed: e.g. a PR
    created by a Manager is approved automatically). The creator's role comes
    from tenants.services.current_user_role - None until login exists, so
    nothing auto-approves yet."""
    if creator_role and creator_role in auto_approve_roles:
        record_decision(
            document,
            ApprovalHistory.DECISION_APPROVED,
            approved_by=f"Auto-approved ({creator_role})",
            reason=f"{doc_label} created by a {creator_role} - auto approval is enabled for that role.",
        )


TAX_NAME_FIELD = {"source": "tax.tax_name", "read_only": True, "default": None}
LINE_AMOUNT_FIELDS = ["taxable_value", "cgst", "sgst", "igst", "cess", "line_total"]


# ---------------------------------------------------------------------------
# Purchase Requisition
# ---------------------------------------------------------------------------


class PurchaseRequisitionItemSerializer(serializers.ModelSerializer):
    # How much of this PR line has already been ordered on POs, and what's
    # left - lets the PR view show conversion progress.
    ordered_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    pending_order_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    item_code = serializers.CharField(source="item.item_code", read_only=True)

    class Meta:
        model = PurchaseRequisitionItem
        fields = [
            "id",
            "item",
            "item_code",
            "description",
            "uom",
            "hsn_sac",
            "quantity",
            "delivery_date",
            "ordered_qty",
            "pending_order_qty",
        ]


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    items = PurchaseRequisitionItemSerializer(many=True)
    pr_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)  # only changed via the approval action, never a plain edit
    approved_by = serializers.CharField(read_only=True)

    class Meta:
        model = PurchaseRequisition
        fields = ["id", "pr_number", "pr_type", "supplier", "raised_by", "pr_date", "status", "approved_by",
                  "remarks", "items"]

    def validate_supplier(self, party: Party | None) -> Party | None:
        # Supplier is optional on a PR (BRD 6.5.1.1.2).
        return check_supplier(self, party, "PR")

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items")
        tenant = self.context["tenant"]
        # A PR type can have its own series (Document "PR", Doc Type = the
        # PR type); otherwise the general PR series is used.
        pr_type = validated_data.get("pr_type", PurchaseRequisition.TYPE_ITEM)
        pr_number = next_number(tenant, doc="PR", doc_type=pr_type)
        pr = PurchaseRequisition.objects.create(tenant=tenant, pr_number=pr_number, **validated_data)
        PurchaseRequisitionItem.objects.bulk_create(
            [PurchaseRequisitionItem(purchase_requisition=pr, **item) for item in items_data]
        )
        auto_approve_for_role(pr, get_company_settings(tenant).auto_approve_pr_roles,
                              self.context.get("user_role"), "PR")
        return pr

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            PurchaseRequisitionItem.objects.bulk_create(
                [PurchaseRequisitionItem(purchase_requisition=instance, **item) for item in items_data]
            )
        return instance


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    # Exposed read-only so the Create Purchase Invoice screen can cap its
    # Invoiced Qty input without recomputing order_qty - invoiced_qty itself.
    pending_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    # Display-only PR reference for the line (BRD 6.5.2.1.2 "PR No.").
    pr_number = serializers.CharField(source="pr_item.purchase_requisition.pr_number", read_only=True, default=None)
    pr_date = serializers.DateField(source="pr_item.purchase_requisition.pr_date", read_only=True, default=None)
    tax_name = serializers.CharField(**TAX_NAME_FIELD)
    item_code = serializers.CharField(source="item.item_code", read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "id",
            "pr_item",
            "pr_number",
            "pr_date",
            "item",
            "item_code",
            "description",
            "uom",
            "hsn_sac",
            "order_qty",
            "invoiced_qty",
            "pending_qty",
            "rate",
            "discount_percent",
            "discount_amount",
            "pack_forward_amount",
            "freight_amount",
            "tax",
            "tax_name",
            *LINE_AMOUNT_FIELDS,
            "delivery_date",
            "notes",
        ]
        read_only_fields = ["invoiced_qty", "discount_amount", *LINE_AMOUNT_FIELDS]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True)
    po_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    approved_by = serializers.CharField(read_only=True)
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    # Every PR this PO draws from, derived from its lines - for the list's
    # "PR No." column.
    pr_numbers = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "po_number",
            "pr_numbers",
            "supplier",
            "po_date",
            "status",
            "raised_by",
            "approved_by",
            "quotation_no",
            "quotation_date",
            "validity_date",
            "kind_attn",
            "other_reference",
            "delivery_to",
            "delivery",
            "terms",
            "payment_terms",
            "currency_code",
            "dispatch_details",
            "remarks",
            "items",
            "net_amount",
        ]

    def get_pr_numbers(self, po: PurchaseOrder) -> list[str]:
        numbers = {line.pr_item.purchase_requisition.pr_number for line in po.items.all() if line.pr_item_id}
        return sorted(numbers)

    def validate_supplier(self, party):
        return check_supplier(self, party, "PO")

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    def validate(self, attrs):
        tenant = self.context["tenant"]
        supplier = attrs.get("supplier") or getattr(self.instance, "supplier", None)

        # Confirmed: every PO must come from a PR, unless Company Settings
        # allows POs without a PR (legacy ISPoWithoutPR).
        if "items" in attrs and not get_company_settings(tenant).allow_po_without_pr:
            if any(item.get("pr_item") is None for item in attrs["items"]):
                raise serializers.ValidationError(
                    {"items": "Every PO line must come from an Approved Purchase Requisition."}
                )

        # What THIS PO already holds against each PR line, so re-saving an
        # existing PO with the same qty doesn't falsely trip the cap.
        already_ordered_here: dict[int, Decimal] = {}
        if self.instance is not None:
            for line in self.instance.items.all():
                if line.pr_item_id:
                    already_ordered_here[line.pr_item_id] = (
                        already_ordered_here.get(line.pr_item_id, ZERO) + line.order_qty
                    )

        requested_per_pr_line: dict[int, Decimal] = {}
        for item in attrs.get("items", []):
            pr_item: PurchaseRequisitionItem | None = item.get("pr_item")
            if pr_item is None:
                continue
            pr = pr_item.purchase_requisition
            if pr.tenant_id != tenant.pk:
                raise serializers.ValidationError({"items": f"PR line {pr_item.id} was not found."})
            if pr.status != PurchaseRequisition.STATUS_APPROVED:
                raise serializers.ValidationError(
                    {"items": f"PR {pr.pr_number} is not Approved - only Approved PRs can be ordered."}
                )
            # A PR raised without a supplier can go to any supplier's PO.
            if pr.supplier_id and supplier and pr.supplier_id != supplier.id:
                raise serializers.ValidationError(
                    {"items": f"PR {pr.pr_number} was raised for a different supplier."}
                )
            if item["item"].id != pr_item.item_id:
                raise serializers.ValidationError(
                    {"items": f"Item on the line for PR {pr.pr_number} does not match the PR's item."}
                )
            requested_per_pr_line[pr_item.id] = requested_per_pr_line.get(pr_item.id, ZERO) + Decimal(
                item["order_qty"]
            )

        for pr_item_id, requested_qty in requested_per_pr_line.items():
            pr_item = PurchaseRequisitionItem.objects.select_related("purchase_requisition").get(pk=pr_item_id)
            available = pr_item.pending_order_qty + already_ordered_here.get(pr_item_id, ZERO)
            if requested_qty > available:
                raise serializers.ValidationError(
                    {
                        "items": (
                            f"Cannot order {requested_qty} of '{pr_item.item}' - only {available} is pending "
                            f"on PR {pr_item.purchase_requisition.pr_number}."
                        )
                    }
                )
        return attrs

    def _lines(self, tenant, supplier, items_data):
        return [price_line(tenant, supplier, item, "order_qty", with_charges=True) for item in items_data]

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items")
        tenant = self.context["tenant"]
        supplier = validated_data["supplier"]

        po_number = next_number(tenant, doc="PO")
        po = PurchaseOrder.objects.create(tenant=tenant, po_number=po_number, **validated_data)
        PurchaseOrderItem.objects.bulk_create(
            [PurchaseOrderItem(purchase_order=po, **line) for line in self._lines(tenant, supplier, items_data)]
        )
        auto_approve_for_role(po, get_company_settings(tenant).auto_approve_po_roles,
                              self.context.get("user_role"), "PO")
        return po

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        tenant = self.context["tenant"]
        supplier = validated_data.get("supplier", instance.supplier)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            PurchaseOrderItem.objects.bulk_create(
                [PurchaseOrderItem(purchase_order=instance, **line)
                 for line in self._lines(tenant, supplier, items_data)]
            )
        return instance


class OrderablePRLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.item_code", read_only=True)
    ordered_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    pending_order_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)

    class Meta:
        model = PurchaseRequisitionItem
        fields = [
            "id",
            "item",
            "item_code",
            "description",
            "uom",
            "hsn_sac",
            "quantity",
            "ordered_qty",
            "pending_order_qty",
            "delivery_date",
        ]


class OrderablePRSerializer(serializers.ModelSerializer):
    """An Approved PR with the lines still left to order - backs the PR
    picker on the Create PO screen, which loads these lines into the grid."""

    lines = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseRequisition
        fields = ["id", "pr_number", "pr_type", "supplier", "pr_date", "lines"]

    def get_lines(self, pr: PurchaseRequisition):
        open_lines = [line for line in pr.items.all() if line.pending_order_qty > ZERO]
        return OrderablePRLineSerializer(open_lines, many=True).data


# ---------------------------------------------------------------------------
# Purchase Invoice
# ---------------------------------------------------------------------------

PI_DOC_TYPE = "PurchaseInvoice"


def post_invoice_stock(invoice: PurchaseInvoice, created_by: str, *, remove: bool = False) -> None:
    """Goods received go into each line's godown (BRD 6.5.3.2 "update stock
    quantity automatically after invoice generation"). remove=True takes
    the invoice's stock back out (cancel/delete)."""
    lines = [] if remove else [
        StockLine(line.item, line.godown, line.invoiced_qty)
        for line in invoice.items.select_related("item", "godown")
        if line.godown_id and tracks_stock(line.item)
    ]
    replace_document_movements(
        invoice.tenant, doc_type=PI_DOC_TYPE, doc_id=invoice.id, doc_number=invoice.invoice_number,
        movement_type="Purchase", movement_date=invoice.invoice_date, lines=lines, created_by=created_by,
    )


def apply_po_deltas(lines, sign: int) -> None:
    """sign=+1 to consume PO pending_qty (create/new lines), -1 to give it
    back (update's "undo the old lines" pass, cancel, delete). Freight
    invoice lines have no PO line and are skipped."""
    for line in lines:
        if not line.po_item_id:
            continue
        po_item = line.po_item
        po_item.invoiced_qty = po_item.invoiced_qty + (sign * line.invoiced_qty)
        po_item.save(update_fields=["invoiced_qty"])


class PurchaseInvoiceItemSerializer(serializers.ModelSerializer):
    returnable_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    secondary_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True, allow_null=True)
    tax_name = serializers.CharField(**TAX_NAME_FIELD)
    godown_name = serializers.CharField(source="godown.name", read_only=True, default=None)
    item_code = serializers.CharField(source="item.item_code", read_only=True)

    class Meta:
        model = PurchaseInvoiceItem
        fields = [
            "id",
            "po_item",
            "item",
            "item_code",
            "description",
            "uom",
            "hsn_sac",
            "invoiced_qty",
            "rate",
            "discount_percent",
            "discount_amount",
            "pack_forward_amount",
            "freight_amount",
            "tax",
            "tax_name",
            *LINE_AMOUNT_FIELDS,
            "godown",
            "godown_name",
            "secondary_uom",
            "conversion_rate",
            "secondary_qty",
            "remarks",
            "returnable_qty",
        ]
        read_only_fields = ["discount_amount", *LINE_AMOUNT_FIELDS]


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    items = PurchaseInvoiceItemSerializer(many=True)
    invoice_number = serializers.CharField(read_only=True)
    po_number = serializers.CharField(source="purchase_order.po_number", read_only=True, default=None)
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            "id",
            "invoice_number",
            "invoice_type",
            "purchase_order",
            "po_number",
            "supplier",
            "invoice_date",
            "supplier_invoice_no",
            "supplier_invoice_date",
            "inward_no",
            "inward_date",
            "purchase_type",
            "due_date",
            "payment_terms",
            "status",
            "remarks",
            "items",
            "net_amount",
        ]
        read_only_fields = ["status"]

    def validate_supplier(self, party):
        return check_supplier(self, party, "Purchase Invoice")

    def validate_purchase_order(self, po: PurchaseOrder | None) -> PurchaseOrder | None:
        # Only an Approved PO can be invoiced - this is the 3-way-match
        # anchor; an invoice against a Pending/Rejected/Hold PO has nothing
        # approved to bill against.
        if po is not None and po.status != po.STATUS_APPROVED:
            raise serializers.ValidationError("Only an Approved Purchase Order can be invoiced.")
        return po

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    def validate(self, attrs):
        tenant = self.context["tenant"]
        old_type = getattr(self.instance, "invoice_type", None)
        changing_type = "invoice_type" in attrs and attrs["invoice_type"] != old_type
        if self.instance is not None and changing_type:
            raise serializers.ValidationError({"invoice_type": "The invoice type can't be changed after saving."})
        invoice_type = attrs.get("invoice_type") or getattr(self.instance, "invoice_type", PurchaseInvoice.TYPE_PO)
        po = attrs["purchase_order"] if "purchase_order" in attrs else getattr(self.instance, "purchase_order", None)
        supplier = attrs.get("supplier") or getattr(self.instance, "supplier", None)
        items = attrs.get("items", [])

        if invoice_type == PurchaseInvoice.TYPE_FREIGHT:
            # Standalone freight bill: no PO, Service items only, each taxed
            # at its own GST rate (agreed rule).
            if po is not None:
                raise serializers.ValidationError({"purchase_order": "A Freight invoice is not linked to a PO."})
            for item in items:
                if item.get("po_item") is not None:
                    raise serializers.ValidationError({"items": "A Freight invoice has no PO lines."})
                if item["item"].item_type != "Service":
                    raise serializers.ValidationError(
                        {"items": f"'{item['item']}' is a Product - a Freight invoice takes Service items only."}
                    )
        else:
            if po is None:
                raise serializers.ValidationError({"purchase_order": "Select the Approved Purchase Order to invoice."})
            if supplier and po.supplier_id != supplier.id:
                raise serializers.ValidationError(
                    {"supplier": "Supplier must match the source Purchase Order's supplier."}
                )
            for item in items:
                po_item: PurchaseOrderItem | None = item.get("po_item")
                if po_item is None or po_item.purchase_order_id != po.id:
                    raise serializers.ValidationError(
                        {"items": "Every line must come from the selected Purchase Order."}
                    )
                if item["item"].id != po_item.item_id:
                    raise serializers.ValidationError({"items": f"Item on PO line {po_item.id} can't be changed."})

        for item in items:
            check_godown(item.get("godown"), tenant)
            if tracks_stock(item["item"]) and item.get("godown") is None:
                raise serializers.ValidationError(
                    {"items": f"Select the godown '{item['item']}' is received into."}
                )

        # Quantity cap per PO line, excluding whatever THIS invoice itself
        # previously consumed (so re-saving with the same qty doesn't trip it).
        already_consumed_here: dict[int, Decimal] = {}
        if self.instance is not None:
            for line in self.instance.items.all():
                if line.po_item_id:
                    already_consumed_here[line.po_item_id] = (
                        already_consumed_here.get(line.po_item_id, ZERO) + line.invoiced_qty
                    )

        requested_per_line: dict[int, Decimal] = {}
        for item in items:
            po_item = item.get("po_item")
            if po_item is not None:
                requested_per_line[po_item.id] = requested_per_line.get(po_item.id, ZERO) + Decimal(
                    item["invoiced_qty"]
                )

        for po_item_id, requested_qty in requested_per_line.items():
            po_item = PurchaseOrderItem.objects.get(pk=po_item_id)
            available = po_item.pending_qty + already_consumed_here.get(po_item_id, ZERO)
            if requested_qty > available:
                raise serializers.ValidationError(
                    {
                        "items": (
                            f"Cannot invoice {requested_qty} of '{po_item.item}' - only {available} is pending "
                            f"on PO {po_item.purchase_order.po_number}."
                        )
                    }
                )
        return attrs

    def _create_lines(self, invoice, tenant, supplier, items_data):
        created = []
        for item in items_data:
            po_item = item.get("po_item")
            line = price_line(tenant, supplier, item, "invoiced_qty", with_charges=True,
                              default_tax=po_item.tax if po_item else None)
            created.append(PurchaseInvoiceItem.objects.create(purchase_invoice=invoice, **line))
        return created

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items")
        tenant = self.context["tenant"]
        supplier = validated_data["supplier"]

        # Freight invoices can have their own series (Document "PI", Doc
        # Type "Freight"); otherwise the general PI series is used.
        invoice_number = next_number(tenant, doc="PI", doc_type=validated_data.get("invoice_type", ""))
        invoice = PurchaseInvoice.objects.create(tenant=tenant, invoice_number=invoice_number, **validated_data)
        apply_po_deltas(self._create_lines(invoice, tenant, supplier, items_data), sign=1)
        post_invoice_stock(invoice, self.context.get("user_name", ""))
        return invoice

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        tenant = self.context["tenant"]
        supplier = validated_data.get("supplier", instance.supplier)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            # Give back whatever the OLD lines consumed before applying the
            # new ones - validate() already accounted for this when checking
            # the cap, so this ordering is safe.
            apply_po_deltas(list(instance.items.all()), sign=-1)
            instance.items.all().delete()
            apply_po_deltas(self._create_lines(instance, tenant, supplier, items_data), sign=1)
        post_invoice_stock(instance, self.context.get("user_name", ""))
        return instance


class ApprovedPOForInvoiceSerializer(serializers.ModelSerializer):
    """Thin shape for the "create Invoice from Approved PO" picker - only
    POs that still have at least one line with pending_qty > 0 are useful
    here, filtered in the view rather than relying on the frontend."""

    class Meta:
        model = PurchaseOrder
        fields = ["id", "po_number", "supplier", "po_date"]


# ---------------------------------------------------------------------------
# Purchase Return
# ---------------------------------------------------------------------------

PRET_DOC_TYPE = "PurchaseReturn"


def post_return_stock(ret: PurchaseReturn, created_by: str, *, remove: bool = False) -> None:
    """Returned goods leave their godown (BRD 6.5.4.2 "update stock quantity
    automatically after Purchase Return created")."""
    lines = [] if remove else [
        StockLine(line.item, line.godown, -line.returned_qty)
        for line in ret.items.select_related("item", "godown")
        if line.godown_id and tracks_stock(line.item)
    ]
    replace_document_movements(
        ret.tenant, doc_type=PRET_DOC_TYPE, doc_id=ret.id, doc_number=ret.return_number,
        movement_type="Purchase Return", movement_date=ret.return_date, lines=lines, created_by=created_by,
    )


def _validate_amount_refunded(document, amount_refunded: Decimal) -> None:
    """Shared by Purchase Return and Debit Note: called after the lines are
    saved, so net_amount reflects the new lines. Raising here rolls back the
    whole save (both create/update run inside transaction.atomic)."""
    if amount_refunded > document.net_amount:
        raise serializers.ValidationError(
            {"amount_refunded": f"Refund amount cannot exceed the net amount ({document.net_amount})."}
        )


class PurchaseReturnItemSerializer(serializers.ModelSerializer):
    tax_name = serializers.CharField(**TAX_NAME_FIELD)
    godown_name = serializers.CharField(source="godown.name", read_only=True, default=None)
    item_code = serializers.CharField(source="item.item_code", read_only=True)

    class Meta:
        model = PurchaseReturnItem
        fields = [
            "id",
            "invoice_item",
            "item",
            "item_code",
            "description",
            "uom",
            "hsn_sac",
            "returned_qty",
            "rate",
            "tax",
            "tax_name",
            "godown",
            "godown_name",
            *LINE_AMOUNT_FIELDS,
        ]
        read_only_fields = LINE_AMOUNT_FIELDS


class PurchaseReturnSerializer(serializers.ModelSerializer):
    items = PurchaseReturnItemSerializer(many=True)
    return_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)  # derived from amount_refunded, never set directly
    amount_refunded = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=ZERO, required=False
    )
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    invoice_number = serializers.CharField(source="purchase_invoice.invoice_number", read_only=True)

    class Meta:
        model = PurchaseReturn
        fields = [
            "id",
            "return_number",
            "purchase_invoice",
            "invoice_number",
            "supplier",
            "return_date",
            "reason",
            "status",
            "remarks",
            "items",
            "net_amount",
            "amount_refunded",
            "balance_amount",
        ]

    def validate_supplier(self, party):
        return check_supplier(self, party, "Purchase Return")

    def validate_purchase_invoice(self, invoice: PurchaseInvoice) -> PurchaseInvoice:
        if invoice.status != invoice.STATUS_ACTIVE:
            raise serializers.ValidationError("Only an Active Purchase Invoice can be the source of a return.")
        return invoice

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    def validate(self, attrs):
        tenant = self.context["tenant"]
        invoice = attrs.get("purchase_invoice") or getattr(self.instance, "purchase_invoice", None)
        supplier = attrs.get("supplier")
        if invoice and supplier and invoice.supplier_id != supplier.id:
            raise serializers.ValidationError({"supplier": "Supplier must match the source Purchase "
            "Invoice's supplier."})

        # Every saved return reserves its quantity (no approval step), so
        # exclude what THIS return already holds when re-saving it.
        already_reserved_here: dict[int, Decimal] = {}
        if self.instance is not None:
            for line in self.instance.items.all():
                already_reserved_here[line.invoice_item_id] = (
                    already_reserved_here.get(line.invoice_item_id, ZERO) + line.returned_qty
                )

        requested_per_line: dict[int, Decimal] = {}
        for item in attrs.get("items", []):
            invoice_item: PurchaseInvoiceItem = item["invoice_item"]
            if invoice and invoice_item.purchase_invoice_id != invoice.id:
                raise serializers.ValidationError(
                    {"items": f"Invoice line {invoice_item.id} does not belong to the selected Purchase Invoice."}
                )
            # Goods go back out of the godown they came into unless the
            # user picked another one.
            item["godown"] = item.get("godown") or invoice_item.godown
            check_godown(item["godown"], tenant)
            if tracks_stock(item["item"]) and item["godown"] is None:
                raise serializers.ValidationError({"items": f"Select the godown '{item['item']}' goes back from."})
            requested_per_line[invoice_item.id] = (
                requested_per_line.get(invoice_item.id, ZERO) + Decimal(item["returned_qty"])
            )

        for invoice_item_id, requested_qty in requested_per_line.items():
            invoice_item = PurchaseInvoiceItem.objects.get(pk=invoice_item_id)
            available = invoice_item.returnable_qty + already_reserved_here.get(invoice_item_id, ZERO)
            if requested_qty > available:
                raise serializers.ValidationError(
                    {
                        "items": (
                            f"Cannot return {requested_qty} of '{invoice_item.item}' - only {available} is "
                            f"returnable on invoice {invoice_item.purchase_invoice.invoice_number}."
                        )
                    }
                )
        return attrs

    def _lines(self, tenant, supplier, items_data):
        # A return mirrors the tax treatment of the invoice line it reverses
        # unless the user changes it.
        return [
            price_line(tenant, supplier, item, "returned_qty", default_tax=item["invoice_item"].tax)
            for item in items_data
        ]

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items")
        tenant = self.context["tenant"]
        supplier = validated_data["supplier"]

        return_number = next_number(tenant, doc="PRET")
        ret = PurchaseReturn.objects.create(tenant=tenant, return_number=return_number, **validated_data)
        PurchaseReturnItem.objects.bulk_create(
            [PurchaseReturnItem(purchase_return=ret, **line) for line in self._lines(tenant, supplier, items_data)]
        )
        _validate_amount_refunded(ret, ret.amount_refunded)
        ret.refresh_status()
        post_return_stock(ret, self.context.get("user_name", ""))
        return ret

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        tenant = self.context["tenant"]
        supplier = validated_data.get("supplier", instance.supplier)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            PurchaseReturnItem.objects.bulk_create(
                [PurchaseReturnItem(purchase_return=instance, **line)
                 for line in self._lines(tenant, supplier, items_data)]
            )
            # The viewset prefetched the OLD lines - drop that cache so
            # net_amount below sees the lines just saved.
            instance._prefetched_objects_cache = {}
        _validate_amount_refunded(instance, instance.amount_refunded)
        instance.refresh_status()
        post_return_stock(instance, self.context.get("user_name", ""))
        return instance


class InvoiceForReturnSerializer(serializers.ModelSerializer):
    """Thin shape for the "pick a Purchase Invoice" pickers on the Create
    Purchase Return and Create Debit Note screens."""

    class Meta:
        model = PurchaseInvoice
        fields = ["id", "invoice_number", "supplier", "invoice_date"]


# ---------------------------------------------------------------------------
# Debit Note
# ---------------------------------------------------------------------------


class DebitNoteItemSerializer(serializers.ModelSerializer):
    tax_name = serializers.CharField(**TAX_NAME_FIELD)
    item_code = serializers.CharField(source="item.item_code", read_only=True)

    class Meta:
        model = DebitNoteItem
        fields = [
            "id",
            "invoice_item",
            "item",
            "item_code",
            "description",
            "uom",
            "hsn_sac",
            "quantity",
            "rate",
            "tax",
            "tax_name",
            *LINE_AMOUNT_FIELDS,
        ]
        read_only_fields = LINE_AMOUNT_FIELDS


class DebitNoteSerializer(serializers.ModelSerializer):
    items = DebitNoteItemSerializer(many=True)
    debit_note_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)  # derived from amount_refunded, or Cancelled via the action
    amount_refunded = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=ZERO, required=False
    )
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    invoice_number = serializers.CharField(source="purchase_invoice.invoice_number", read_only=True, default=None)

    class Meta:
        model = DebitNote
        fields = [
            "id",
            "debit_note_number",
            "purchase_invoice",
            "invoice_number",
            "supplier",
            "debit_note_date",
            "reason",
            "status",
            "remarks",
            "items",
            "net_amount",
            "amount_refunded",
            "balance_amount",
        ]

    def validate_supplier(self, party):
        return check_supplier(self, party, "Debit Note")

    def validate_purchase_invoice(self, invoice: PurchaseInvoice | None) -> PurchaseInvoice | None:
        if invoice is not None and invoice.status != invoice.STATUS_ACTIVE:
            raise serializers.ValidationError("Only an Active Purchase Invoice can be linked to a Debit Note.")
        return invoice

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    def validate(self, attrs):
        if "purchase_invoice" in attrs:
            invoice = attrs["purchase_invoice"]
        else:
            invoice = getattr(self.instance, "purchase_invoice", None)
        supplier = attrs.get("supplier")
        if invoice and supplier and invoice.supplier_id != supplier.id:
            raise serializers.ValidationError(
                {"supplier": "Supplier must match the linked Purchase Invoice's supplier."}
            )

        for item in attrs.get("items", []):
            invoice_item: PurchaseInvoiceItem | None = item.get("invoice_item")
            if invoice_item is None:
                continue
            if invoice is None or invoice_item.purchase_invoice_id != invoice.id:
                raise serializers.ValidationError(
                    {"items": f"Invoice line {invoice_item.id} does not belong to the linked Purchase Invoice."}
                )
        return attrs

    def _lines(self, tenant, supplier, items_data):
        return [
            price_line(tenant, supplier, item, "quantity",
                       default_tax=item["invoice_item"].tax if item.get("invoice_item") else None)
            for item in items_data
        ]

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items")
        tenant = self.context["tenant"]
        supplier = validated_data["supplier"]

        debit_note_number = next_number(tenant, doc="DN")
        dn = DebitNote.objects.create(tenant=tenant, debit_note_number=debit_note_number, **validated_data)
        DebitNoteItem.objects.bulk_create(
            [DebitNoteItem(debit_note=dn, **line) for line in self._lines(tenant, supplier, items_data)]
        )
        _validate_amount_refunded(dn, dn.amount_refunded)
        dn.refresh_status()
        return dn

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        tenant = self.context["tenant"]
        supplier = validated_data.get("supplier", instance.supplier)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            DebitNoteItem.objects.bulk_create(
                [DebitNoteItem(debit_note=instance, **line) for line in self._lines(tenant, supplier, items_data)]
            )
            # The viewset prefetched the OLD lines - drop that cache so
            # net_amount below sees the lines just saved.
            instance._prefetched_objects_cache = {}
        _validate_amount_refunded(instance, instance.amount_refunded)
        instance.refresh_status()
        return instance
