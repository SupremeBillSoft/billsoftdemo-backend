# sales/serializers.py
#
# Validation and save logic for the sales documents. Every priced document
# goes through PricedDocumentSerializer: number it, price it with
# sales.pricing, sync its lines in place (amendments keep line ids, so the
# challans / invoices that point at them stay linked), then run the
# document's after-save rules (stock, statuses).

import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import ProtectedError
from rest_framework import serializers

from docnumbering.services import next_number
from inventory.services import tracks_stock
from parties.models import Party
from tax.models import Tax
from tenants.services import get_company_settings,next_doc_number 
#from units.models import Unit
from .compliance import rules
from .models import (
    AdditionalCharge,
    ChallanReturn,
    ChallanReturnItem,
    CreditNote,
    CreditNoteItem,
    DeliveryChallan,
    DeliveryChallanItem,
    Enquiry,
    EnquiryFollowUp,
    EWayBill,
    Quotation,
    QuotationItem,
    QuotationItemBreakUp,
    SaleOrder,
    SaleOrderItem,
    SaleOrderRevision,
    SalesInvoice,
    SalesInvoiceItem,
    SalesReturn,
    SalesReturnItem,
    Item,
)
from .pricing import PricingOptions, price_document
from .services import (
    check_amended_lines,
    post_challan_return_stock,
    post_challan_stock,
    post_invoice_stock,
    post_sales_return_stock,
    record_amendment,
    refresh_sale_order_status,
    sale_order_has_fulfilment,
)
import re

ZERO = Decimal("0")
BLOCKED_PARTY_STATUSES = {"Inactive", "Suspend/Hold"}
COMPUTED_LINE_FIELDS = ["taxable_value", "cgst", "sgst", "igst", "cess", "line_total"]


def check_customer(serializer, party: Party | None, doc_label: str, *, export: bool = False) -> Party | None:
    """Same eligibility rule as purchase suppliers: new documents can't use
    an Inactive / Suspended party, and the party must be a customer."""
    if party is None:
        return party
    tenant = serializer.context["tenant"]
    if party.tenant_id != tenant.pk:
        raise serializers.ValidationError("Customer not found.")
    current = getattr(serializer.instance, "customer", None)
    if current is None or current.pk != party.pk:
        if party.status in BLOCKED_PARTY_STATUSES:
            raise serializers.ValidationError(
                f"Customer '{party.party_name}' is {party.status} and cannot be used on a new {doc_label}."
            )
        if export and not party.is_export_customer:
            raise serializers.ValidationError(f"'{party.party_name}' is not marked as an export customer.")
        if not export and not (party.is_customer or party.is_export_customer):
            raise serializers.ValidationError(f"'{party.party_name}' is not marked as a customer.")
    return party


def check_tenant_object(obj, tenant, label: str):
    if obj is not None and getattr(obj, "tenant_id", tenant.pk) != tenant.pk:
        raise serializers.ValidationError({"items": f"{label} not found."})


# ---------------------------------------------------------------------------
# Shared priced-document machinery
# ---------------------------------------------------------------------------


class AdditionalChargeSerializer(serializers.ModelSerializer):
    tax_name = serializers.CharField(source="tax.tax_name", read_only=True, default=None)

    class Meta:
        model = AdditionalCharge
        fields = ["id", "name", "amount", "tax", "tax_name", "cgst", "sgst", "igst", "cess", "total"]
        read_only_fields = ["id", "cgst", "sgst", "igst", "cess", "total"]


PRICED_LINE_FIELDS = [
    "id",
    "item",
    "item_code",
    "description",
    "uom",
    "hsn_sac",
    "quantity",
    "rate",
    "discount_percent",
    "discount_amount",
    "tax",
    "tax_name",
    *COMPUTED_LINE_FIELDS,
]


class PricedLineSerializer(serializers.ModelSerializer):
    # Writable so an edit can say "this is existing line 12" - lines are
    # updated in place, not deleted and recreated.
    id = serializers.IntegerField(required=False)
    item_code = serializers.CharField(source="item.item_code", read_only=True)
    tax_name = serializers.CharField(source="tax.tax_name", read_only=True, default=None)

    class Meta:
        fields = PRICED_LINE_FIELDS
        read_only_fields = COMPUTED_LINE_FIELDS

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than 0.")
        return value

    def validate_tax(self, tax: Tax | None):
        if tax is not None and tax.status != "Active":
            raise serializers.ValidationError(f"Tax '{tax.tax_name}' is Inactive.")
        return tax


PRICED_DOC_FIELDS = [
    "customer",
    "customer_name",
    "place_of_supply",
    "price_includes_tax",
    "doc_discount_type",
    "doc_discount_value",
    "doc_discount_before_tax",
    "round_off_enabled",
    "adjustment_amount",
    "total_taxable",
    "total_cgst",
    "total_sgst",
    "total_igst",
    "total_cess",
    "total_charges",
    "doc_discount_amount",
    "round_off_amount",
    "net_amount",
    "remarks",
    "custom_fields",
    "charges",
    "items",
    "created_by",
    "created_at",
    "updated_at",
]
PRICED_DOC_READ_ONLY = [
    "total_taxable",
    "total_cgst",
    "total_sgst",
    "total_igst",
    "total_cess",
    "total_charges",
    "doc_discount_amount",
    "round_off_amount",
    "net_amount",
    "created_by",
    "created_at",
    "updated_at",
]


class PricedDocumentSerializer(serializers.ModelSerializer):
    """Subclasses set: line_model, line_fk, number_field, doc_code, label."""

    line_model = None
    line_fk = ""
    number_field = ""
    doc_code = ""
    label = "document"

    charges = AdditionalChargeSerializer(many=True, required=False)
    customer_name = serializers.CharField(source="customer.party_name", read_only=True)

    # --- hooks -----------------------------------------------------------
    def numbering(self, obj) -> tuple[str, str, str]:
        return self.doc_code, "", ""

    def pricing_options(self, obj) -> PricingOptions:
        return PricingOptions(
            buyer_state=self.buyer_state(obj),
            price_includes_tax=obj.price_includes_tax,
            doc_discount_type=obj.doc_discount_type,
            doc_discount_value=obj.doc_discount_value,
            doc_discount_before_tax=obj.doc_discount_before_tax,
            round_off_enabled=obj.round_off_enabled,
            adjustment_amount=obj.adjustment_amount,
        )

    def buyer_state(self, obj) -> str:
        return obj.place_of_supply or getattr(obj, "ship_to_state", "") or obj.customer.state

    def before_save(self, obj, items: list[dict] | None, created: bool) -> None:
        """Runs inside the transaction before lines are priced."""

    def after_save(self, obj, created: bool) -> None:
        """Runs inside the transaction once lines and totals are saved."""

    # --- validation ------------------------------------------------------
    def validate_customer(self, party):
        return check_customer(self, party, self.label)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        tenant = self.context["tenant"]
        for item in items:
            check_tenant_object(item["item"], tenant, "Item")
            check_tenant_object(item.get("tax"), tenant, "Tax")
        return items

    # --- save ------------------------------------------------------------
    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items")
        charges = validated_data.pop("charges", [])
        tenant = self.context["tenant"]
        obj = self.Meta.model(tenant=tenant, created_by=self.context.get("user_name", ""), **validated_data)
        doc, doc_type, doc_sub_type = self.numbering(obj)
        setattr(obj, self.number_field, next_number(tenant, doc=doc, doc_type=doc_type, doc_sub_type=doc_sub_type))
        self.before_save(obj, items, created=True)
        obj.save()
        self._apply_lines(obj, items, charges)
        self.after_save(obj, created=True)
        return obj

    @transaction.atomic
    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        charges = validated_data.pop("charges", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        self.before_save(instance, items, created=False)
        instance.save()
        if items is None:
            items = [self._line_as_data(line) for line in instance.items.all()]
        if charges is None:
            charges = [{"name": c.name, "amount": c.amount, "tax": c.tax} for c in instance.charges.all()]
        self._apply_lines(instance, items, charges)
        self.after_save(instance, created=False)
        return instance

    def _line_as_data(self, line) -> dict:
        data = {"id": line.id}
        for field in self.line_model._meta.concrete_fields:
            if field.name in ("id", self.line_fk, *COMPUTED_LINE_FIELDS):
                continue
            data[field.name] = getattr(line, field.name)
        return data

    def _apply_lines(self, obj, items: list[dict], charges: list[dict]) -> None:
        tenant = self.context["tenant"]
        result = price_document(tenant, items, charges, self.pricing_options(obj))

        existing = {line.id: line for line in self.line_model.objects.filter(**{self.line_fk: obj})}
        keep = set()
        for data in result.lines:
            line_id = data.pop("id", None)
            if line_id in existing:
                line = existing[line_id]
                for key, value in data.items():
                    setattr(line, key, value)
                line.save()
                keep.add(line_id)
            else:
                self.line_model.objects.create(**{self.line_fk: obj}, **data)
        stale = [line for line_id, line in existing.items() if line_id not in keep]
        try:
            for line in stale:
                line.delete()
        except ProtectedError as exc:
            raise serializers.ValidationError(
                {"items": "A line already used on a later document can't be removed."}
            ) from exc

        obj.charges.all().delete()
        for charge in result.charges:
            charge.pop("id", None)
            AdditionalCharge.objects.create(document=obj, **charge)

        for key, value in result.totals.items():
            setattr(obj, key, value)
        obj.save()
        obj._prefetched_objects_cache = {}


# ---------------------------------------------------------------------------
# Enquiry
# ---------------------------------------------------------------------------


class EnquiryFollowUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnquiryFollowUp
        fields = [
            "id",
            "follow_up_date",
            "follow_up_time",
            "mode",
            "notes",
            "outcome",
            "is_done",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]

    def validate_follow_up_date(self, value):
        unchanged = self.instance is not None and self.instance.follow_up_date == value
        if not unchanged and value < datetime.date.today():
            raise serializers.ValidationError("Follow-up date cannot be in the past.")
        return value


def follow_up_state(enquiry: Enquiry, today: datetime.date) -> str:
    """Drives the list colours (BRD "differentiate with colors based on
    Expired and Upcoming follow-ups")."""
    if enquiry.status == Enquiry.STATUS_CLOSED:
        return "closed"
    upcoming = enquiry.next_follow_up
    if upcoming is None:
        return "none"
    if upcoming.follow_up_date < today:
        return "overdue"
    if upcoming.follow_up_date == today:
        return "today"
    return "upcoming"


class EnquirySerializer(serializers.ModelSerializer):
    enquiry_number = serializers.CharField(read_only=True)
    follow_ups = EnquiryFollowUpSerializer(many=True, read_only=True)
    next_follow_up_date = serializers.DateField(required=False, allow_null=True)
    next_follow_up_time = serializers.TimeField(required=False, allow_null=True)
    follow_up_state = serializers.SerializerMethodField()

    class Meta:
        model = Enquiry
        fields = [
            "id",
            "enquiry_number",
            "enquiry_date",
            "enquiry_mode",
            "reference_by",
            "area",
            "industry_type",
            "company_type",
            "customer",
            "customer_name",
            "address",
            "volume",
            "feasibility",
            "business_type",
            "contact_person",
            "contact_number",
            "email",
            "website",
            "enquiry_details",
            "division",
            "prepared_by",
            "status",
            "custom_fields",
            "follow_ups",
            "next_follow_up_date",
            "next_follow_up_time",
            "follow_up_state",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        upcoming = instance.next_follow_up
        data["next_follow_up_date"] = upcoming.follow_up_date.isoformat() if upcoming else None
        data["next_follow_up_time"] = (
            upcoming.follow_up_time.isoformat() if upcoming and upcoming.follow_up_time else None
        )
        return data

    def get_follow_up_state(self, obj) -> str:
        return follow_up_state(obj, datetime.date.today())

    def validate_contact_number(self, value):
        if value and not value.isdigit():
            raise serializers.ValidationError("Contact number must be numeric.")
        return value

    def validate_next_follow_up_date(self, value):
        if value and value < datetime.date.today():
            raise serializers.ValidationError("Follow-up date cannot be in the past.")
        return value

    def validate(self, attrs):
        # Feasibility = No closes the enquiry (BRD 6.4.1.2 2.3).
        feasibility = attrs.get("feasibility", getattr(self.instance, "feasibility", None))
        if feasibility is False:
            attrs["status"] = Enquiry.STATUS_CLOSED
        status = attrs.get("status", getattr(self.instance, "status", Enquiry.STATUS_OPEN))
        # Follow-up is required while the enquiry is open, optional once closed.
        if self.instance is None and status == Enquiry.STATUS_OPEN and not attrs.get("next_follow_up_date"):
            raise serializers.ValidationError(
                {"next_follow_up_date": "Next follow-up date is required for an open enquiry."}
            )
        customer = attrs.get("customer")
        if customer is not None:
            check_tenant_object(customer, self.context["tenant"], "Customer")
            attrs.setdefault("customer_name", customer.party_name)
        if not attrs.get("customer_name", getattr(self.instance, "customer_name", "")):
            raise serializers.ValidationError({"customer_name": "Customer name is required."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        follow_date = validated_data.pop("next_follow_up_date", None)
        follow_time = validated_data.pop("next_follow_up_time", None)
        tenant = self.context["tenant"]
        enquiry = Enquiry.objects.create(
            tenant=tenant, enquiry_number=next_number(tenant, doc="Enquiry"), **validated_data
        )
        if follow_date:
            EnquiryFollowUp.objects.create(
                enquiry=enquiry,
                follow_up_date=follow_date,
                follow_up_time=follow_time,
                created_by=self.context.get("user_name", ""),
            )
        return enquiry

    @transaction.atomic
    def update(self, instance, validated_data):
        follow_date = validated_data.pop("next_follow_up_date", None)
        follow_time = validated_data.pop("next_follow_up_time", None)
        instance = super().update(instance, validated_data)
        upcoming = instance.next_follow_up
        if follow_date and (upcoming is None or upcoming.follow_up_date != follow_date):
            EnquiryFollowUp.objects.create(
                enquiry=instance,
                follow_up_date=follow_date,
                follow_up_time=follow_time,
                created_by=self.context.get("user_name", ""),
            )
        return instance


# ---------------------------------------------------------------------------
# Quotation (BRD 6.4.2)
# ---------------------------------------------------------------------------
# quotation/serializers.py
# Keep your existing imports for PricedLineSerializer, PricedDocumentSerializer,
# PRICED_LINE_FIELDS, PRICED_DOC_FIELDS, PRICED_DOC_READ_ONLY, check_tenant_object.


EST_DIRECT = "Direct"
EST_BREAKUP = "Breakup"          # same strings as the frontend grid
 
TWO_PLACES = Decimal("0.01")
 
 
def _desc_key(text: str) -> str:
    """'Steel  Rod' and 'steel rod' count as the same description."""
    return " ".join((text or "").split()).casefold()
 
class QuotationItemBreakUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuotationItemBreakUp
        fields = ["id", "sl_no", "description", "hsn_sac", "quantity", "uom", "rate", "amount"]
        read_only_fields = ["id", "sl_no", "amount"]      # sl_no / amount are set by the server
 
    def validate_quantity(self, value):
        if value < 0:
            raise serializers.ValidationError("Quantity can't be negative.")
        return value
 
    def validate_rate(self, value):
        if value < 0:
            raise serializers.ValidationError("Rate can't be negative.")
        return value
 
 
class QuotationItemSerializer(PricedLineSerializer):
    # Quotation lines are typed in, so there is no Item Master pick: accept null.
    item = serializers.PrimaryKeyRelatedField(queryset=Item.objects.all(), allow_null=True, required=False)
    breakups = QuotationItemBreakUpSerializer(many=True, required=False)
 
    class Meta(PricedLineSerializer.Meta):
        model = QuotationItem
        fields = [*PRICED_LINE_FIELDS, "estimation_type", "breakups"]
 
    def validate(self, attrs):
        attrs = super().validate(attrs)
        est = attrs.get("estimation_type", EST_DIRECT)
        if est not in (EST_DIRECT, EST_BREAKUP):
            raise serializers.ValidationError({"estimation_type": "Must be Direct or Breakup."})
        has_breakups = bool(attrs.get("breakups"))
        if est == EST_BREAKUP and not has_breakups:
            raise serializers.ValidationError({"breakups": "Add at least one sub item to a Breakup line."})
        if est == EST_DIRECT and has_breakups:
            raise serializers.ValidationError({"breakups": "A Direct line can't have sub items."})
        return attrs
 
 
class QuotationSerializer(PricedDocumentSerializer):
    line_model = QuotationItem
    line_fk = "quotation"
    number_field = "quotation_number"
    doc_code = "Quotation"
    label = "Quotation"
 
    items = QuotationItemSerializer(many=True)
    quotation_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    is_expired = serializers.SerializerMethodField()
    enquiry_number = serializers.CharField(source="enquiry.enquiry_number", read_only=True, default=None)
 
    class Meta:
        model = Quotation
        fields = [
            "id",
            "quotation_number",
            "quotation_year_code",
            "quotation_date",
            "valid_till",
            "enquiry",
            "enquiry_number",
            "contact_person",
            "kind_attn",
            "terms",
            "delivery",
            "packages",
            "follow_up_date",
            "status",
            "is_expired",
            *PRICED_DOC_FIELDS,
        ]
        read_only_fields = PRICED_DOC_READ_ONLY
 
    def get_is_expired(self, obj) -> bool:
        return obj.is_expired(datetime.date.today())
 
    # --- validation -------------------------------------------------------
    def validate_enquiry(self, enquiry):
        check_tenant_object(enquiry, self.context["tenant"], "Enquiry")
        return enquiry
 
    def validate_quotation_year_code(self, value):
        # The frontend calculates the year code; we only guard the format.
        if not re.fullmatch(r"\d{4}", value):
            raise serializers.ValidationError("Year code must be 4 digits, e.g. 2526.")
        return value
 
    def validate_items(self, items):
        items = super().validate_items(items)          # non-empty + tenant checks
 
        # no repeated description among the lines, nor among one line's sub items
        line_seen = {}
        for i, item in enumerate(items, start=1):
            key = _desc_key(item.get("description", ""))
            if key in line_seen:
                raise serializers.ValidationError(
                    f"Line {i}: description '{item['description'].strip()}' is already used on line {line_seen[key]}."
                )
            line_seen[key] = i
 
            sub_seen = {}
            for j, b in enumerate(item.get("breakups", []), start=1):
                sub_key = _desc_key(b.get("description", ""))
                if not sub_key:
                    raise serializers.ValidationError(f"Line {i}.{j}: sub item description is required.")
                if sub_key in sub_seen:
                    raise serializers.ValidationError(
                        f"Line {i}.{j}: sub item '{b['description'].strip()}' is already used on {i}.{sub_seen[sub_key]}."
                    )
                sub_seen[sub_key] = j
 
        for item in items:
            breakups = item.get("breakups", [])
            for b in breakups:
                b["amount"] = (b["quantity"] * b["rate"]).quantize(TWO_PLACES)
            if item.get("estimation_type") == EST_BREAKUP:
                # same rule as the grid: a Breakup line's Rate = SUM of its sub item rates
                item["rate"] = sum((b["rate"] for b in breakups), Decimal("0")).quantize(TWO_PLACES)
        return items
 
    # --- numbering: tenant + year code, max + 1 ---------------------------
    def before_save(self, obj, items, created):
        # Runs inside the base class's transaction, before obj.save(), so the
        # advisory lock taken by next_doc_number covers the INSERT.
        if created:
            obj.quotation_number = next_doc_number(
                obj.tenant, Quotation,
                no_field="quotation_number",
                year_field="quotation_year_code",
                year_code=obj.quotation_year_code,
            )
 
    # --- breakup persistence ----------------------------------------------
    @staticmethod
    def _extract_breakups(validated_data):
        """Pull breakups out of each line BEFORE the base class sees them.
        Returns [(posted line id or None, [breakup dicts])] in payload order."""
        return [(line.get("id"), line.pop("breakups", []))
                for line in validated_data.get("items", [])]
 
    @staticmethod
    def _save_breakups(quotation, payload, existing_ids):
        lines = {line.id: line for line in quotation.items.all()}
        # lines created by this save, in the order they were posted
        new_lines = iter(quotation.items.exclude(id__in=existing_ids).order_by("id"))
        rows = []
        for line_id, breakups in payload:
            # same rule as the base class: an id it doesn't know is a NEW line
            line = lines[line_id] if line_id in existing_ids else next(new_lines)
            line.breakups.all().delete()               # sub items replaced as a set per line
            rows += [
                QuotationItemBreakUp(quotation_item=line, sl_no=n, **b)
                for n, b in enumerate(breakups, start=1)
            ]
        QuotationItemBreakUp.objects.bulk_create(rows)
 
    # --- create / update ---------------------------------------------------
    def create(self, validated_data):
        payload = self._extract_breakups(validated_data)
        with transaction.atomic():
            quotation = super().create(validated_data)
            self._save_breakups(quotation, payload, existing_ids=set())
        return quotation
 
    def update(self, instance, validated_data):
        validated_data.pop("quotation_year_code", None)     # fixed after creation
        payload = self._extract_breakups(validated_data)
        existing_ids = set(instance.items.values_list("id", flat=True))
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            self._save_breakups(instance, payload, existing_ids)
        return instance
 
# ---------------------------------------------------------------------------
# Sale Order
# ---------------------------------------------------------------------------


class SaleOrderItemSerializer(PricedLineSerializer):
    delivered_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    invoiced_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    pending_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)

    class Meta(PricedLineSerializer.Meta):
        model = SaleOrderItem
        fields = [
            *PRICED_LINE_FIELDS,
            "amortisation_cost",
            "drawing_no",
            "drawing_revision",
            "line_no",
            "short_closed_qty",
            "delivered_qty",
            "invoiced_qty",
            "pending_qty",
        ]
        read_only_fields = [*COMPUTED_LINE_FIELDS, "short_closed_qty"]


class SaleOrderRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleOrderRevision
        fields = ["id", "revision_no", "amendment_date", "reason", "snapshot", "created_by", "created_at"]


class SaleOrderSerializer(PricedDocumentSerializer):
    line_model = SaleOrderItem
    line_fk = "sale_order"
    number_field = "so_number"
    doc_code = "SO"
    label = "Sale Order"

    items = SaleOrderItemSerializer(many=True)
    so_number = serializers.CharField(read_only=True)
    display_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    revision_no = serializers.IntegerField(read_only=True)
    amendment_date = serializers.DateField(read_only=True)
    quotation_number = serializers.CharField(source="quotation.quotation_number", read_only=True, default=None)
    advance_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    # Required only when amending an order that has started fulfilment.
    amendment_reason = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = SaleOrder
        fields = [
            "id",
            "so_number",
            "display_number",
            "so_date",
            "so_type",
            "quotation",
            "quotation_number",
            "customer_po_no",
            "customer_po_date",
            "valid_upto",
            "currency_code",
            "exchange_rate",
            "status",
            "closed_reason",
            "revision_no",
            "amendment_date",
            "amendment_reason",
            "advance_received",
            "advance_mode",
            "advance_balance",
            *PRICED_DOC_FIELDS,
        ]
        read_only_fields = [*PRICED_DOC_READ_ONLY, "closed_reason"]

    def numbering(self, obj):
        return "SO", obj.so_type, ""

    def pricing_options(self, obj):
        options = super().pricing_options(obj)
        options.export = obj.so_type == SaleOrder.TYPE_EXPORT
        return options

    def validate(self, attrs):
        so_type = attrs.get("so_type", getattr(self.instance, "so_type", SaleOrder.TYPE_DOMESTIC))
        customer = attrs.get("customer")
        if customer is not None and so_type == SaleOrder.TYPE_EXPORT:
            check_customer(self, customer, "Export Sale Order", export=True)
        quotation = attrs.get("quotation")
        # Only a newly linked quotation is checked - the one an order was
        # converted from is "Converted" by then, and amending must still work.
        if quotation is not None and (self.instance is None or quotation.pk != self.instance.quotation_id):
            check_tenant_object(quotation, self.context["tenant"], "Quotation")
            if quotation.status not in (Quotation.STATUS_ACCEPTED, Quotation.STATUS_SENT, Quotation.STATUS_DRAFT):
                raise serializers.ValidationError({"quotation": "This quotation can no longer be converted."})
        return attrs

    def after_save(self, obj, created):
        if created and obj.quotation_id:
            Quotation.objects.filter(pk=obj.quotation_id).update(status=Quotation.STATUS_CONVERTED)
        refresh_sale_order_status(obj)

    def create(self, validated_data):
        validated_data.pop("amendment_reason", None)
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        reason = validated_data.pop("amendment_reason", "") or ""
        # Once anything has been delivered / invoiced, an edit is an
        # amendment: snapshot the order as it is NOW (before applying the
        # changes), then save it as the next revision under the same number.
        if sale_order_has_fulfilment(instance):
            if "items" in validated_data:
                check_amended_lines(instance, validated_data["items"])
            record_amendment(instance, reason=reason, created_by=self.context.get("user_name", ""))
        return super().update(instance, validated_data)


# ---------------------------------------------------------------------------
# Delivery Challan
# ---------------------------------------------------------------------------


class DeliveryChallanItemSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    item_code = serializers.CharField(source="item.item_code", read_only=True)
    godown_name = serializers.CharField(source="godown.name", read_only=True, default=None)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    invoiced_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    pending_invoice_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    returned_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    so_pending_qty = serializers.DecimalField(
        source="so_item.pending_qty", max_digits=14, decimal_places=3, read_only=True, default=None
    )

    class Meta:
        model = DeliveryChallanItem
        fields = [
            "id",
            "so_item",
            "item",
            "item_code",
            "description",
            "uom",
            "hsn_sac",
            "quantity",
            "rejection_qty",
            "rate",
            "amount",
            "godown",
            "godown_name",
            "remarks",
            "invoiced_qty",
            "pending_invoice_qty",
            "returned_qty",
            "so_pending_qty",
        ]

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than 0.")
        return value


class DeliveryChallanSerializer(serializers.ModelSerializer):
    items = DeliveryChallanItemSerializer(many=True)
    dc_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    customer_name = serializers.CharField(source="customer.party_name", read_only=True)
    so_number = serializers.CharField(source="sale_order.so_number", read_only=True, default=None)
    invoice_status = serializers.SerializerMethodField()
    return_status = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryChallan
        fields = [
            "id",
            "dc_number",
            "dc_date",
            "dc_type",
            "purpose",
            "tax_nature",
            "customer",
            "customer_name",
            "sale_order",
            "so_number",
            "customer_po_no",
            "customer_po_date",
            "kind_attn",
            "to_place",
            "location",
            "transport_mode",
            "vehicle_no",
            "inward_dc_no",
            "inward_dc_date",
            "approx_value",
            "returnable",
            "goods_category",
            "return_due_date",
            "ship_to_name",
            "ship_to_address1",
            "ship_to_address2",
            "ship_to_city",
            "ship_to_state",
            "ship_to_pincode",
            "ship_to_gstin",
            "status",
            "cancel_reason",
            "remarks",
            "items",
            "invoice_status",
            "return_status",
            "total_quantity",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["return_due_date", "cancel_reason", "created_by", "created_at"]

    def get_total_quantity(self, obj) -> str:
        return str(sum((line.quantity for line in obj.items.all()), ZERO))

    def get_invoice_status(self, obj) -> str:
        if not obj.is_billable:
            return "Non billable"
        lines = list(obj.items.all())
        invoiced = sum((line.invoiced_qty for line in lines), ZERO)
        total = sum((line.quantity for line in lines), ZERO)
        if invoiced <= 0:
            return "Not invoiced"
        return "Invoiced" if invoiced >= total else "Partially invoiced"

    def get_return_status(self, obj) -> str | None:
        if not obj.returnable:
            return None
        lines = list(obj.items.all())
        returned = sum((line.returned_qty for line in lines), ZERO)
        total = sum((line.quantity for line in lines), ZERO)
        if returned >= total:
            return "Returned"
        if obj.return_due_date and obj.return_due_date < datetime.date.today():
            return "Overdue"
        return "Partially returned" if returned > 0 else "Awaiting return"

    def validate_customer(self, party):
        return check_customer(self, party, "Delivery Challan")

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    def validate(self, attrs):
        tenant = self.context["tenant"]
        dc_type = attrs.get("dc_type", getattr(self.instance, "dc_type", DeliveryChallan.TYPE_DIRECT))
        purpose = attrs.get("purpose", getattr(self.instance, "purpose", DeliveryChallan.PURPOSE_SUPPLY))
        order = attrs["sale_order"] if "sale_order" in attrs else getattr(self.instance, "sale_order", None)
        customer = attrs.get("customer") or getattr(self.instance, "customer", None)
        items = attrs.get("items", [])

        if dc_type == DeliveryChallan.TYPE_NON_BILLABLE:
            if purpose == DeliveryChallan.PURPOSE_SUPPLY:
                raise serializers.ValidationError(
                    {"purpose": "Say why goods move without an invoice (job work, approval, repair, sample...)."}
                )
        else:
            attrs["purpose"] = DeliveryChallan.PURPOSE_SUPPLY
        if purpose == DeliveryChallan.PURPOSE_JOB_WORK:
            attrs["returnable"] = True

        if dc_type == DeliveryChallan.TYPE_ORDER:
            if order is None:
                raise serializers.ValidationError({"sale_order": "Select the Sale Order being delivered."})
            check_tenant_object(order, tenant, "Sale Order")
            if order.status == SaleOrder.STATUS_CLOSED:
                raise serializers.ValidationError({"sale_order": "This Sale Order is closed."})
            if customer and order.customer_id != customer.id:
                raise serializers.ValidationError({"customer": "Customer must match the Sale Order."})
        elif order is not None:
            raise serializers.ValidationError({"sale_order": "Only an order-based challan is linked to a Sale Order."})

        held_here = {}
        if self.instance is not None:
            for line in self.instance.items.all():
                if line.so_item_id:
                    held_here[line.so_item_id] = held_here.get(line.so_item_id, ZERO) + line.quantity
        wanted = {}
        for item in items:
            check_tenant_object(item["item"], tenant, "Item")
            so_item = item.get("so_item")
            if dc_type == DeliveryChallan.TYPE_ORDER:
                if so_item is None or so_item.sale_order_id != order.id:
                    raise serializers.ValidationError({"items": "Every line must come from the selected Sale Order."})
                if item["item"].id != so_item.item_id:
                    raise serializers.ValidationError({"items": f"Item on order line {so_item.id} can't be changed."})
                wanted[so_item.id] = wanted.get(so_item.id, ZERO) + Decimal(item["quantity"])
            elif so_item is not None:
                raise serializers.ValidationError({"items": "Order lines belong on an order-based challan."})
            godown = item.get("godown")
            if godown is not None and godown.tenant_id != tenant.pk:
                raise serializers.ValidationError({"items": "Godown not found."})
            if tracks_stock(item["item"]) and godown is None:
                raise serializers.ValidationError({"items": f"Select the godown '{item['item']}' is dispatched from."})
        for so_item_id, qty in wanted.items():
            so_item = SaleOrderItem.objects.get(pk=so_item_id)
            available = so_item.pending_qty + held_here.get(so_item_id, ZERO)
            if qty > available:
                raise serializers.ValidationError(
                    {"items": f"Cannot deliver {qty} of '{so_item.item}' - only {available} is pending on the order."}
                )
        return attrs

    def _save_items(self, challan, items):
        existing = {line.id: line for line in challan.items.all()}
        keep = set()
        for data in items:
            line_id = data.pop("id", None)
            if line_id in existing:
                line = existing[line_id]
                for key, value in data.items():
                    setattr(line, key, value)
                line.save()
                keep.add(line_id)
            else:
                DeliveryChallanItem.objects.create(delivery_challan=challan, **data)
        try:
            for line_id, line in existing.items():
                if line_id not in keep:
                    line.delete()
        except ProtectedError as exc:
            raise serializers.ValidationError(
                {"items": "A line already invoiced or returned can't be removed."}
            ) from exc
        challan._prefetched_objects_cache = {}

    def _finish(self, challan):
        if not challan.approx_value:
            challan.approx_value = sum((line.quantity * line.rate for line in challan.items.all()), ZERO)
        if challan.returnable:
            challan.return_due_date = rules.job_work_return_due(
                sent_on=challan.dc_date, capital_goods=challan.goods_category == DeliveryChallan.GOODS_CAPITAL
            )
        else:
            challan.return_due_date = None
        challan.save()
        challan._stock_warnings = post_challan_stock(challan, self.context.get("user_name", ""))
        if challan.sale_order_id:
            refresh_sale_order_status(challan.sale_order)

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items")
        tenant = self.context["tenant"]
        sub_type = "Returnable" if validated_data.get("returnable") else ""
        challan = DeliveryChallan.objects.create(
            tenant=tenant,
            created_by=self.context.get("user_name", ""),
            dc_number=next_number(tenant, doc="DC", doc_type=validated_data.get("dc_type", ""), doc_sub_type=sub_type),
            **validated_data,
        )
        self._save_items(challan, items)
        self._finish(challan)
        return challan

    @transaction.atomic
    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items is not None:
            self._save_items(instance, items)
        self._finish(instance)
        return instance


class ChallanReturnItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChallanReturnItem
        fields = ["id", "dc_item", "quantity", "godown"]


class ChallanReturnSerializer(serializers.ModelSerializer):
    items = ChallanReturnItemSerializer(many=True)

    class Meta:
        model = ChallanReturn
        fields = [
            "id",
            "delivery_challan",
            "return_date",
            "party_dc_no",
            "remarks",
            "items",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["delivery_challan", "created_by", "created_at"]

    def validate(self, attrs):
        challan: DeliveryChallan = self.context["delivery_challan"]
        if not challan.returnable:
            raise serializers.ValidationError("This challan is not returnable.")
        if challan.status == DeliveryChallan.STATUS_CANCELLED:
            raise serializers.ValidationError("This challan is cancelled.")
        for item in attrs.get("items", []):
            dc_item = item["dc_item"]
            if dc_item.delivery_challan_id != challan.id:
                raise serializers.ValidationError({"items": "Line does not belong to this challan."})
            outstanding = dc_item.quantity - dc_item.returned_qty
            if item["quantity"] <= 0 or item["quantity"] > outstanding:
                raise serializers.ValidationError(
                    {"items": f"'{dc_item.item}': up to {outstanding} can still come back."}
                )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items")
        challan = self.context["delivery_challan"]
        receipt = ChallanReturn.objects.create(
            delivery_challan=challan, created_by=self.context.get("user_name", ""), **validated_data
        )
        for item in items:
            ChallanReturnItem.objects.create(challan_return=receipt, **item)
        post_challan_return_stock(receipt, self.context.get("user_name", ""))
        if all(line.returned_qty >= line.quantity for line in challan.items.all()):
            challan.status = DeliveryChallan.STATUS_CLOSED
            challan.save(update_fields=["status", "updated_at"])
        return receipt


# ---------------------------------------------------------------------------
# Sales Invoice
# ---------------------------------------------------------------------------


class SalesInvoiceItemSerializer(PricedLineSerializer):
    godown_name = serializers.CharField(source="godown.name", read_only=True, default=None)
    returnable_qty = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)

    class Meta(PricedLineSerializer.Meta):
        model = SalesInvoiceItem
        fields = [
            *PRICED_LINE_FIELDS,
            "so_item",
            "dc_item",
            "godown",
            "godown_name",
            "rejection_qty",
            "is_free",
            "remarks",
            "returnable_qty",
        ]


class SalesInvoiceSerializer(PricedDocumentSerializer):
    line_model = SalesInvoiceItem
    line_fk = "sales_invoice"
    number_field = "invoice_number"
    doc_code = "Invoice"
    label = "Sales Invoice"

    items = SalesInvoiceItemSerializer(many=True)
    invoice_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    balance_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    so_number = serializers.CharField(source="sale_order.so_number", read_only=True, default=None)
    dc_numbers = serializers.SerializerMethodField()
    stock_warnings = serializers.SerializerMethodField()

    class Meta:
        model = SalesInvoice
        fields = [
            "id",
            "invoice_number",
            "invoice_date",
            "invoice_type",
            "tax_nature",
            "sale_order",
            "so_number",
            "quotation",
            "delivery_challans",
            "dc_numbers",
            "customer_po_no",
            "customer_po_date",
            "po_amendment_no",
            "po_amendment_date",
            "our_dc_ref",
            "your_dc_ref",
            "kind_attn",
            "transport_mode",
            "vehicle_no",
            "removal_datetime",
            "distance_km",
            "bank",
            "payment_terms",
            "due_date",
            "ship_to_name",
            "ship_to_address1",
            "ship_to_address2",
            "ship_to_city",
            "ship_to_state",
            "ship_to_pincode",
            "ship_to_gstin",
            "employee_code",
            "employee_name",
            "counter_no",
            "currency_code",
            "exchange_rate",
            "exporter_ref",
            "export_with_igst",
            "lut_number",
            "port_code",
            "shipping_bill_no",
            "shipping_bill_date",
            "container_no",
            "goods_description",
            "advance_adjusted",
            "amount_received",
            "payment_mode",
            "payment_reference",
            "balance_amount",
            "status",
            "cancel_reason",
            "cancelled_at",
            "einvoice_status",
            "irn",
            "ack_no",
            "ack_date",
            "signed_qr",
            "irn_cancelled_at",
            "stock_warnings",
            *PRICED_DOC_FIELDS,
        ]
        read_only_fields = [
            *PRICED_DOC_READ_ONLY,
            "cancel_reason",
            "cancelled_at",
            "einvoice_status",
            "irn",
            "ack_no",
            "ack_date",
            "signed_qr",
            "irn_cancelled_at",
        ]

    def get_dc_numbers(self, obj) -> list[str]:
        return [c.dc_number for c in obj.delivery_challans.all()]

    def get_stock_warnings(self, obj) -> list[str]:
        return getattr(obj, "_stock_warnings", [])

    def numbering(self, obj):
        if obj.invoice_type == SalesInvoice.TYPE_PROFORMA:
            # Proforma has its own series - it is not a tax invoice.
            return "InvoiceProforma", "", ""
        return "Invoice", obj.invoice_type, obj.tax_nature

    def pricing_options(self, obj):
        options = super().pricing_options(obj)
        options.export = obj.invoice_type == SalesInvoice.TYPE_EXPORT
        options.zero_rated = options.export and not obj.export_with_igst
        return options

    def validate(self, attrs):
        tenant = self.context["tenant"]
        invoice_type = attrs.get("invoice_type", getattr(self.instance, "invoice_type", SalesInvoice.TYPE_DIRECT))
        if self.instance is not None and invoice_type != self.instance.invoice_type:
            raise serializers.ValidationError({"invoice_type": "The invoice type can't be changed after saving."})
        customer = attrs.get("customer") or getattr(self.instance, "customer", None)
        order = attrs["sale_order"] if "sale_order" in attrs else getattr(self.instance, "sale_order", None)
        challans = attrs.get("delivery_challans")
        if challans is None and self.instance is not None:
            challans = list(self.instance.delivery_challans.all())
        challans = challans or []
        items = attrs.get("items", [])

        if invoice_type == SalesInvoice.TYPE_EXPORT:
            if "customer" in attrs:
                check_customer(self, attrs["customer"], "Export Invoice", export=True)
            if not attrs.get("export_with_igst", getattr(self.instance, "export_with_igst", False)):
                lut = (
                    attrs.get("lut_number")
                    or getattr(self.instance, "lut_number", "")
                    or get_company_settings(tenant).lut_number
                )
                if not lut:
                    raise serializers.ValidationError(
                        {"lut_number": "Export without IGST needs a valid LUT number (set it in Company Settings)."}
                    )
                attrs["lut_number"] = lut

        if order is not None:
            check_tenant_object(order, tenant, "Sale Order")
            if customer and order.customer_id != customer.id:
                raise serializers.ValidationError({"customer": "Customer must match the Sale Order."})
        for challan in challans:
            check_tenant_object(challan, tenant, "Delivery Challan")
            if invoice_type != SalesInvoice.TYPE_DC:
                raise serializers.ValidationError({"delivery_challans": "Only a DC-based invoice links challans."})
            if not challan.is_billable or challan.status == DeliveryChallan.STATUS_CANCELLED:
                raise serializers.ValidationError({"delivery_challans": f"{challan.dc_number} can't be invoiced."})
            if customer and challan.customer_id != customer.id:
                raise serializers.ValidationError({"delivery_challans": "All challans must be for this customer."})
        if invoice_type == SalesInvoice.TYPE_DC and not challans:
            raise serializers.ValidationError({"delivery_challans": "Select the challan(s) being invoiced."})

        own_so, own_dc = {}, {}
        if self.instance is not None and self.instance.status == SalesInvoice.STATUS_ACTIVE:
            for line in self.instance.items.all():
                if line.so_item_id and not line.dc_item_id:
                    own_so[line.so_item_id] = own_so.get(line.so_item_id, ZERO) + line.quantity
                if line.dc_item_id:
                    own_dc[line.dc_item_id] = own_dc.get(line.dc_item_id, ZERO) + line.quantity
        want_so, want_dc = {}, {}
        challan_ids = {c.id for c in challans}
        moves_stock = invoice_type != SalesInvoice.TYPE_PROFORMA
        for item in items:
            so_item, dc_item = item.get("so_item"), item.get("dc_item")
            if invoice_type == SalesInvoice.TYPE_DC:
                if dc_item is None or dc_item.delivery_challan_id not in challan_ids:
                    raise serializers.ValidationError({"items": "Every line must come from the selected challan(s)."})
                if item["item"].id != dc_item.item_id:
                    raise serializers.ValidationError({"items": "The item on a challan line can't be changed."})
                want_dc[dc_item.id] = want_dc.get(dc_item.id, ZERO) + Decimal(item["quantity"])
                item["so_item"] = dc_item.so_item
                # The challan already moved this stock - a godown here would
                # only invite a second deduction.
                item["godown"] = None
            elif dc_item is not None:
                raise serializers.ValidationError({"items": "Challan lines belong on a DC-based invoice."})
            if so_item is not None and invoice_type != SalesInvoice.TYPE_DC:
                if invoice_type not in (SalesInvoice.TYPE_ORDER, SalesInvoice.TYPE_EXPORT, SalesInvoice.TYPE_PROFORMA):
                    raise serializers.ValidationError({"items": "Order lines belong on an order-based invoice."})
                check_tenant_object(so_item.sale_order, tenant, "Sale Order")
                if customer and so_item.sale_order.customer_id != customer.id:
                    raise serializers.ValidationError({"items": "Order lines must be this customer's."})
                if item["item"].id != so_item.item_id:
                    raise serializers.ValidationError({"items": "The item on an order line can't be changed."})
                if invoice_type != SalesInvoice.TYPE_PROFORMA:
                    want_so[so_item.id] = want_so.get(so_item.id, ZERO) + Decimal(item["quantity"])
            if invoice_type == SalesInvoice.TYPE_ORDER and so_item is None:
                raise serializers.ValidationError(
                    {"items": "Every line of an order-based invoice comes from an order."}
                )
            godown = item.get("godown")
            if godown is not None and godown.tenant_id != tenant.pk:
                raise serializers.ValidationError({"items": "Godown not found."})
            needs_godown = moves_stock and dc_item is None and tracks_stock(item["item"])
            if needs_godown and godown is None:
                raise serializers.ValidationError({"items": f"Select the godown '{item['item']}' is sold from."})

        for so_item_id, qty in want_so.items():
            so_item = SaleOrderItem.objects.get(pk=so_item_id)
            available = so_item.pending_qty + own_so.get(so_item_id, ZERO)
            if qty > available:
                raise serializers.ValidationError(
                    {"items": f"Cannot invoice {qty} of '{so_item.item}' - only {available} is pending on the order."}
                )
        for dc_item_id, qty in want_dc.items():
            dc_item = DeliveryChallanItem.objects.get(pk=dc_item_id)
            available = dc_item.pending_invoice_qty + own_dc.get(dc_item_id, ZERO)
            if qty > available:
                raise serializers.ValidationError(
                    {"items": f"Cannot invoice {qty} of '{dc_item.item}' - only {available} is pending on the challan."}
                )
        return attrs

    def before_save(self, obj, items, created):
        if obj.invoice_type == SalesInvoice.TYPE_PROFORMA:
            obj.einvoice_status = SalesInvoice.EINV_NOT_APPLICABLE
        elif obj.einvoice_status in (SalesInvoice.EINV_NOT_APPLICABLE, SalesInvoice.EINV_PENDING):
            applicable = rules.einvoice_applicable(
                enabled=get_company_settings(obj.tenant).einvoice_enabled,
                customer_gstin=obj.customer.gst_no,
                is_export=obj.is_export,
                is_proforma=False,
            )
            obj.einvoice_status = SalesInvoice.EINV_PENDING if applicable else SalesInvoice.EINV_NOT_APPLICABLE

    def after_save(self, obj, created):
        if obj.amount_received + obj.advance_adjusted > obj.net_amount:
            raise serializers.ValidationError(
                {"amount_received": f"Received + advance can't exceed the invoice total ({obj.net_amount})."}
            )
        obj._stock_warnings = post_invoice_stock(obj, self.context.get("user_name", ""))
        if created and obj.quotation_id:
            Quotation.objects.filter(pk=obj.quotation_id).update(status=Quotation.STATUS_CONVERTED)
        orders = {
            line.so_item.sale_order for line in obj.items.select_related("so_item__sale_order") if line.so_item_id
        }
        for order in orders:
            refresh_sale_order_status(order)
        for challan in obj.delivery_challans.all():
            refresh_challan_status(challan)

    def create(self, validated_data):
        challans = validated_data.pop("delivery_challans", [])
        self._challans = challans
        return super().create(validated_data)

    def update(self, instance, validated_data):
        challans = validated_data.pop("delivery_challans", None)
        if challans is not None:
            instance.delivery_challans.set(challans)
        return super().update(instance, validated_data)

    def _apply_lines(self, obj, items, charges):
        if getattr(self, "_challans", None):
            obj.delivery_challans.set(self._challans)
            self._challans = None
        super()._apply_lines(obj, items, charges)


def refresh_challan_status(challan: DeliveryChallan) -> None:
    """A billable challan closes once every line is fully invoiced and
    re-opens if an invoice is cancelled."""
    if challan.status == DeliveryChallan.STATUS_CANCELLED or not challan.is_billable or challan.returnable:
        return
    done = all(line.pending_invoice_qty <= 0 for line in challan.items.all())
    status = DeliveryChallan.STATUS_CLOSED if done else DeliveryChallan.STATUS_OPEN
    if status != challan.status:
        challan.status = status
        challan.save(update_fields=["status", "updated_at"])


# ---------------------------------------------------------------------------
# Sales Return and Credit Note
# ---------------------------------------------------------------------------


def _validate_refund(document) -> None:
    if document.amount_refunded > document.net_amount:
        raise serializers.ValidationError(
            {"amount_refunded": f"Refund amount cannot exceed the net amount ({document.net_amount})."}
        )


class SalesReturnItemSerializer(PricedLineSerializer):
    godown_name = serializers.CharField(source="godown.name", read_only=True, default=None)

    class Meta(PricedLineSerializer.Meta):
        model = SalesReturnItem
        fields = [*PRICED_LINE_FIELDS, "invoice_item", "godown", "godown_name"]


class SalesReturnSerializer(PricedDocumentSerializer):
    line_model = SalesReturnItem
    line_fk = "sales_return"
    number_field = "return_number"
    doc_code = "SalesReturn"
    label = "Sales Return"

    items = SalesReturnItemSerializer(many=True)
    return_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    balance_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    invoice_number = serializers.CharField(source="sales_invoice.invoice_number", read_only=True)
    amount_refunded = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=ZERO, required=False)

    class Meta:
        model = SalesReturn
        fields = [
            "id",
            "return_number",
            "return_date",
            "sales_invoice",
            "invoice_number",
            "reason",
            "payment_terms",
            "due_date",
            "amount_refunded",
            "balance_amount",
            "status",
            "cancel_reason",
            *PRICED_DOC_FIELDS,
        ]
        read_only_fields = [*PRICED_DOC_READ_ONLY, "cancel_reason"]

    def buyer_state(self, obj):
        invoice = obj.sales_invoice
        return obj.place_of_supply or invoice.place_of_supply or invoice.ship_to_state or obj.customer.state

    def pricing_options(self, obj):
        options = super().pricing_options(obj)
        invoice = obj.sales_invoice
        options.export = invoice.is_export
        options.zero_rated = invoice.is_export and not invoice.export_with_igst
        options.price_includes_tax = False
        return options

    def validate(self, attrs):
        tenant = self.context["tenant"]
        invoice = attrs.get("sales_invoice") or getattr(self.instance, "sales_invoice", None)
        check_tenant_object(invoice, tenant, "Sales Invoice")
        if invoice.status != SalesInvoice.STATUS_ACTIVE or invoice.invoice_type == SalesInvoice.TYPE_PROFORMA:
            raise serializers.ValidationError({"sales_invoice": "Only an active tax invoice can be returned against."})
        customer = attrs.get("customer") or getattr(self.instance, "customer", None)
        if customer and invoice.customer_id != customer.id:
            raise serializers.ValidationError({"customer": "Customer must match the invoice."})
        held = {}
        if self.instance is not None and self.instance.status != SalesReturn.STATUS_CANCELLED:
            for line in self.instance.items.all():
                held[line.invoice_item_id] = held.get(line.invoice_item_id, ZERO) + line.quantity
        wanted = {}
        for item in attrs.get("items", []):
            invoice_item = item["invoice_item"]
            if invoice_item.sales_invoice_id != invoice.id:
                raise serializers.ValidationError({"items": "Every line must come from the selected invoice."})
            item["item"] = invoice_item.item
            item.setdefault("tax", invoice_item.tax)
            item["godown"] = (
                item.get("godown")
                or invoice_item.godown
                or (invoice_item.dc_item.godown if invoice_item.dc_item_id else None)
            )
            if tracks_stock(invoice_item.item) and item["godown"] is None:
                raise serializers.ValidationError({"items": f"Select the godown '{invoice_item.item}' comes back to."})
            wanted[invoice_item.id] = wanted.get(invoice_item.id, ZERO) + Decimal(item["quantity"])
        for invoice_item_id, qty in wanted.items():
            invoice_item = SalesInvoiceItem.objects.get(pk=invoice_item_id)
            available = invoice_item.returnable_qty + held.get(invoice_item_id, ZERO)
            if qty > available:
                raise serializers.ValidationError(
                    {"items": f"Cannot return {qty} of '{invoice_item.item}' - only {available} can be returned."}
                )
        return attrs

    def after_save(self, obj, created):
        _validate_refund(obj)
        obj.refresh_status()
        post_sales_return_stock(obj, self.context.get("user_name", ""))


class CreditNoteItemSerializer(PricedLineSerializer):
    class Meta(PricedLineSerializer.Meta):
        model = CreditNoteItem
        fields = [*PRICED_LINE_FIELDS, "invoice_item"]


class CreditNoteSerializer(PricedDocumentSerializer):
    line_model = CreditNoteItem
    line_fk = "credit_note"
    number_field = "credit_note_number"
    doc_code = "CreditNote"
    label = "Credit Note"

    items = CreditNoteItemSerializer(many=True)
    credit_note_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    balance_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    invoice_number = serializers.CharField(source="sales_invoice.invoice_number", read_only=True)
    return_number = serializers.CharField(source="sales_return.return_number", read_only=True, default=None)
    amount_refunded = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=ZERO, required=False)
    gst_deadline = serializers.SerializerMethodField()

    class Meta:
        model = CreditNote
        fields = [
            "id",
            "credit_note_number",
            "credit_note_date",
            "sales_invoice",
            "invoice_number",
            "sales_return",
            "return_number",
            "reason",
            "payment_terms",
            "amount_refunded",
            "balance_amount",
            "status",
            "cancel_reason",
            "gst_deadline",
            "einvoice_status",
            "irn",
            "ack_no",
            "ack_date",
            "signed_qr",
            "irn_cancelled_at",
            *PRICED_DOC_FIELDS,
        ]
        read_only_fields = [
            *PRICED_DOC_READ_ONLY,
            "cancel_reason",
            "einvoice_status",
            "irn",
            "ack_no",
            "ack_date",
            "signed_qr",
            "irn_cancelled_at",
        ]

    def get_gst_deadline(self, obj) -> str:
        return rules.credit_note_deadline(
            invoice_date=obj.sales_invoice.invoice_date,
            fy_start_month=get_company_settings(obj.tenant).financial_year_start_month,
        ).isoformat()

    def buyer_state(self, obj):
        invoice = obj.sales_invoice
        return obj.place_of_supply or invoice.place_of_supply or invoice.ship_to_state or obj.customer.state

    def pricing_options(self, obj):
        options = super().pricing_options(obj)
        invoice = obj.sales_invoice
        options.export = invoice.is_export
        options.zero_rated = invoice.is_export and not invoice.export_with_igst
        return options

    def validate(self, attrs):
        tenant = self.context["tenant"]
        invoice = attrs.get("sales_invoice") or getattr(self.instance, "sales_invoice", None)
        check_tenant_object(invoice, tenant, "Sales Invoice")
        if invoice.status != SalesInvoice.STATUS_ACTIVE or invoice.invoice_type == SalesInvoice.TYPE_PROFORMA:
            raise serializers.ValidationError({"sales_invoice": "A credit note needs an active tax invoice."})
        customer = attrs.get("customer") or getattr(self.instance, "customer", None)
        if customer and invoice.customer_id != customer.id:
            raise serializers.ValidationError({"customer": "Customer must match the invoice."})

        note_date = attrs.get("credit_note_date") or getattr(self.instance, "credit_note_date", None)
        deadline = rules.credit_note_deadline(
            invoice_date=invoice.invoice_date, fy_start_month=get_company_settings(tenant).financial_year_start_month
        )
        if note_date and note_date > deadline:
            raise serializers.ValidationError(
                {
                    "credit_note_date": (
                        f"A GST credit note for invoice {invoice.invoice_number} had to be issued by "
                        f"{deadline:%d %b %Y} "
                        "(CGST Act s.34). Give a commercial credit without GST instead."
                    )
                }
            )

        reason = attrs.get("reason", getattr(self.instance, "reason", CreditNote.REASON_OTHER))
        sales_return = (
            attrs.get("sales_return") if "sales_return" in attrs else getattr(self.instance, "sales_return", None)
        )
        if reason == CreditNote.REASON_SALES_RETURN and sales_return is None:
            raise serializers.ValidationError({"sales_return": "Select the Sales Return this credit note is for."})
        if sales_return is not None and sales_return.sales_invoice_id != invoice.id:
            raise serializers.ValidationError({"sales_return": "That return is for a different invoice."})
        for item in attrs.get("items", []):
            invoice_item = item.get("invoice_item")
            if invoice_item is not None:
                if invoice_item.sales_invoice_id != invoice.id:
                    raise serializers.ValidationError({"items": "Lines must come from the linked invoice."})
                item.setdefault("tax", invoice_item.tax)
        return attrs

    def before_save(self, obj, items, created):
        if obj.einvoice_status in (CreditNote.EINV_NOT_APPLICABLE, CreditNote.EINV_PENDING):
            applicable = rules.einvoice_applicable(
                enabled=get_company_settings(obj.tenant).einvoice_enabled,
                customer_gstin=obj.customer.gst_no,
                is_export=obj.sales_invoice.is_export,
                is_proforma=False,
            )
            obj.einvoice_status = CreditNote.EINV_PENDING if applicable else CreditNote.EINV_NOT_APPLICABLE

    def after_save(self, obj, created):
        _validate_refund(obj)
        obj.refresh_status()


# ---------------------------------------------------------------------------
# E-invoice / E-way bill requests
# ---------------------------------------------------------------------------


class CancelSerializer(serializers.Serializer):
    # IRP / EWB reason codes: 1 Duplicate, 2 Data entry mistake,
    # 3 Order cancelled, 4 Others.
    reason_code = serializers.ChoiceField(choices=["1", "2", "3", "4"], default="2")
    remark = serializers.CharField(max_length=100)


class EWayBillRequestSerializer(serializers.Serializer):
    transport_mode = serializers.ChoiceField(choices=["Road", "Rail", "Air", "Ship"], default="Road")
    vehicle_type = serializers.ChoiceField(
        choices=[EWayBill.VEHICLE_REGULAR, EWayBill.VEHICLE_ODC], default=EWayBill.VEHICLE_REGULAR
    )
    vehicle_no = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    transporter_id = serializers.CharField(max_length=15, required=False, allow_blank=True, default="")
    transporter_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    transport_doc_no = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    transport_doc_date = serializers.DateField(required=False, allow_null=True, default=None)
    distance_km = serializers.IntegerField(min_value=1, max_value=4000)


class UpdateVehicleSerializer(serializers.Serializer):
    vehicle_no = serializers.CharField(max_length=20)
    reason = serializers.CharField(max_length=100)
    place = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class EWayBillSerializer(serializers.ModelSerializer):
    document_type = serializers.SerializerMethodField()

    class Meta:
        model = EWayBill
        fields = [
            "id",
            "document_type",
            "object_id",
            "document_number",
            "ewb_number",
            "ewb_date",
            "valid_till",
            "supply_type",
            "sub_type",
            "transport_mode",
            "vehicle_type",
            "vehicle_no",
            "transporter_id",
            "transporter_name",
            "transport_doc_no",
            "transport_doc_date",
            "distance_km",
            "consignment_value",
            "status",
            "cancel_reason",
            "cancelled_at",
            "vehicle_history",
            "created_by",
            "created_at",
        ]

    def get_document_type(self, obj) -> str:
        return "DeliveryChallan" if obj.content_type.model == "deliverychallan" else "SalesInvoice"


class CustomerItemSerializer(serializers.Serializer):
    """Legacy "Part Code Load": the customer's mapped items with the part
    code printed on their documents (Party Master > Items)."""

    item = serializers.IntegerField(source="item_id")
    item_code = serializers.CharField(source="item.item_code")
    item_name = serializers.CharField(source="item.item_name")
    part_code = serializers.CharField(source="item_code_in_print")
    discount_percentage = serializers.FloatField()
