# sales/models.py
#
# BRD 6.4 Sales & Marketing. Document chain:
#
#   Enquiry -> Quotation -> Sale Order -> Delivery Challan -> Sales Invoice
#                                     \___________________/      |
#                                      (order-based invoice)      +-> Sales Return
#                                                                  +-> Credit Note
#   E-Invoice (IRN) and E-Way Bill hang off invoices / credit notes / challans.
#
# Design decisions (see the Sales decision record for the reasoning):
#  - Quantity on a Sale Order line is FULFILLED by either a Delivery Challan
#    or an order-based invoice (whichever happens first); a DC-based invoice
#    consumes the challan, never the order again. Delivered / invoiced /
#    pending are derived from the downstream lines, never stored counters.
#  - Stock leaves the godown when goods physically move: on the Delivery
#    Challan if there is one, otherwise on the invoice. A DC-based invoice
#    does not move stock again. Proforma invoices never move stock.
#  - Sale Order amendments keep the same number and add a revision (with a
#    snapshot of the previous version) once anything has been fulfilled.
#  - Posted invoices are corrected through Credit Notes, not edited, once an
#    IRN exists or goods have been returned.

from decimal import Decimal

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Sum

from bank.models import Bank
from inventory.models import Godown
from items.models import Item
from parties.models import Party
from purchase.models import (
    REFUND_STATUS_CHOICES,
    REFUND_STATUS_UNPAID,
    refund_status_for,
)
from tax.models import Tax
from tenants.models import Tenant

ZERO = Decimal("0")


def _money(**kwargs):
    return models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, **kwargs)


def _qty(**kwargs):
    return models.DecimalField(max_digits=14, decimal_places=3, **kwargs)


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class AdditionalCharge(models.Model):
    """Transport / packing / handling charges on any priced document (BRD
    "Add Another Charge"), each with its own tax applicability."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    document = GenericForeignKey("content_type", "object_id")

    name = models.CharField(max_length=100)
    amount = _money()
    tax = models.ForeignKey(Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cgst = _money()
    sgst = _money()
    igst = _money()
    cess = _money()
    total = _money()

    class Meta:
        db_table = "sales_additional_charge"
        ordering = ["id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]


class PricedLine(models.Model):
    """Common line pricing: qty x rate, line discount (% or amount, each
    derived from the other), tax per line (BRD "auto fetch Tax % from Item
    master, allow user to change"), stored GST breakdown."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    uom = models.CharField(max_length=50, blank=True, default="")
    hsn_sac = models.CharField(max_length=20, blank=True, default="")
    quantity = _qty()
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    discount_amount = _money()
    tax = models.ForeignKey(Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    taxable_value = _money()
    cgst = _money()
    sgst = _money()
    igst = _money()
    cess = _money()
    line_total = _money()

    class Meta:
        abstract = True
        ordering = ["id"]


class PricedDocument(models.Model):
    """Header fields shared by Quotation, Sale Order, Invoice, Return and
    Credit Note: customer, place of supply, tax-inclusive pricing,
    document discount (before or after tax), round-off and adjustment, and
    the stored totals."""

    DISCOUNT_PERCENT = "Percent"
    DISCOUNT_AMOUNT = "Amount"
    DISCOUNT_CHOICES = [(DISCOUNT_PERCENT, "Percent"), (DISCOUNT_AMOUNT, "Amount")]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="+")
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    # State the supply is made to - decides CGST+SGST vs IGST. Defaults to
    # the ship-to / customer state when left blank.
    place_of_supply = models.CharField(max_length=50, blank=True, default="")
    price_includes_tax = models.BooleanField(default=False)

    doc_discount_type = models.CharField(max_length=10, choices=DISCOUNT_CHOICES, default=DISCOUNT_PERCENT)
    doc_discount_value = _money()
    # Before tax: recorded on the invoice, so it reduces the taxable value
    # (CGST Act s.15(3)(a)). After tax: reduces only the amount payable.
    doc_discount_before_tax = models.BooleanField(default=True)
    round_off_enabled = models.BooleanField(default=True)
    adjustment_amount = _money()

    total_taxable = _money()
    total_cgst = _money()
    total_sgst = _money()
    total_igst = _money()
    total_cess = _money()
    total_charges = _money()
    doc_discount_amount = _money()
    round_off_amount = _money()
    net_amount = _money()

    remarks = models.TextField(blank=True, default="")
    custom_fields = models.JSONField(default=list, blank=True)
    created_by = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    charges = GenericRelation(AdditionalCharge)

    class Meta:
        abstract = True


class EInvoiceFields(models.Model):
    """IRN details returned by the Invoice Registration Portal (BRD 6.4.6)."""

    EINV_NOT_APPLICABLE = "NotApplicable"
    EINV_PENDING = "Pending"
    EINV_GENERATED = "Generated"
    EINV_CANCELLED = "Cancelled"
    EINV_CHOICES = [
        (EINV_NOT_APPLICABLE, "Not applicable"),
        (EINV_PENDING, "Pending"),
        (EINV_GENERATED, "Generated"),
        (EINV_CANCELLED, "Cancelled"),
    ]

    einvoice_status = models.CharField(max_length=20, choices=EINV_CHOICES, default=EINV_NOT_APPLICABLE)
    irn = models.CharField(max_length=64, blank=True, default="")
    ack_no = models.CharField(max_length=30, blank=True, default="")
    ack_date = models.DateTimeField(null=True, blank=True)
    signed_qr = models.TextField(blank=True, default="")
    irn_cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Enquiry (BRD 6.4.1)
# ---------------------------------------------------------------------------


class Enquiry(models.Model):
    STATUS_OPEN = "Open"
    STATUS_CLOSED = "Closed"
    STATUS_CHOICES = [(STATUS_OPEN, "Open"), (STATUS_CLOSED, "Closed")]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="+")
    enquiry_number = models.CharField(max_length=50)
    enquiry_date = models.DateField()

    # Customer + source info. An enquiry often comes from a prospect who is
    # not a Party yet, so the master link is optional and the name is kept.
    enquiry_mode = models.CharField(max_length=30, blank=True, default="")
    reference_by = models.CharField(max_length=100, blank=True, default="")
    area = models.CharField(max_length=100, blank=True, default="")
    industry_type = models.CharField(max_length=100, blank=True, default="")
    company_type = models.CharField(max_length=100, blank=True, default="")
    customer = models.ForeignKey(Party, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    customer_name = models.CharField(max_length=150)
    address = models.TextField(blank=True, default="")
    volume = models.CharField(max_length=50, blank=True, default="")
    # None = not assessed yet; False closes the enquiry automatically.
    feasibility = models.BooleanField(null=True, blank=True)

    business_type = models.CharField(max_length=50, blank=True, default="")
    contact_person = models.CharField(max_length=100, blank=True, default="")
    contact_number = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(max_length=100, blank=True, default="")
    website = models.CharField(max_length=150, blank=True, default="")
    enquiry_details = models.TextField(blank=True, default="")
    division = models.CharField(max_length=100, blank=True, default="")
    prepared_by = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    custom_fields = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_enquiry"
        ordering = ["-enquiry_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "enquiry_number"], name="uniq_enquiry_number_per_tenant")
        ]

    def __str__(self):
        return f"{self.enquiry_number} ({self.customer_name})"

    @property
    def next_follow_up(self):
        pending = [f for f in self.follow_ups.all() if not f.is_done]
        return min(pending, key=lambda f: (f.follow_up_date, f.follow_up_time or "")) if pending else None


class EnquiryFollowUp(models.Model):
    """BRD: "System should allow multiple follow-ups per enquiry"."""

    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="follow_ups")
    follow_up_date = models.DateField()
    follow_up_time = models.TimeField(null=True, blank=True)
    mode = models.CharField(max_length=30, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    outcome = models.TextField(blank=True, default="")
    is_done = models.BooleanField(default=False)
    created_by = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_enquiry_follow_up"
        ordering = ["follow_up_date", "follow_up_time", "id"]


# ---------------------------------------------------------------------------
# Quotation (BRD 6.4.2)
# ---------------------------------------------------------------------------


class Quotation(PricedDocument):
    STATUS_DRAFT = "Draft"
    STATUS_SENT = "Sent"
    STATUS_ACCEPTED = "Accepted"
    STATUS_DECLINED = "Declined"
    STATUS_CONVERTED = "Converted"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SENT, "Sent"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CONVERTED, "Converted"),
    ]
    # BRD: "not allow to modify the Quotation after its accepted".
    LOCKED_STATUSES = {STATUS_ACCEPTED, STATUS_DECLINED, STATUS_CONVERTED}

    quotation_number = models.CharField(max_length=50)
    quotation_date = models.DateField()
    valid_till = models.DateField(null=True, blank=True)
    enquiry = models.ForeignKey(Enquiry, on_delete=models.SET_NULL, null=True, blank=True, related_name="quotations")
    contact_person = models.CharField(max_length=100, blank=True, default="")
    kind_attn = models.CharField(max_length=100, blank=True, default="")
    terms = models.TextField(blank=True, default="")
    delivery = models.CharField(max_length=255, blank=True, default="")
    packages = models.CharField(max_length=100, blank=True, default="")
    follow_up_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    class Meta:
        db_table = "sales_quotation"
        ordering = ["-quotation_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "quotation_number"], name="uniq_quotation_number_per_tenant")
        ]

    def is_expired(self, today) -> bool:
        # Derived, not stored: a Draft/Sent quotation past Valid Till.
        return bool(
            self.valid_till and self.valid_till < today and self.status in (self.STATUS_DRAFT, self.STATUS_SENT)
        )


class QuotationItem(PricedLine):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="items")

    class Meta(PricedLine.Meta):
        db_table = "sales_quotation_item"


# ---------------------------------------------------------------------------
# Sale Order (BRD 6.4.3)
# ---------------------------------------------------------------------------


class SaleOrder(PricedDocument):
    TYPE_DOMESTIC = "Domestic"
    TYPE_EXPORT = "Export"
    TYPE_CHOICES = [(TYPE_DOMESTIC, "Sale Order"), (TYPE_EXPORT, "Export Sale Order")]

    STATUS_PENDING = "Pending"
    STATUS_COMPLETED = "Completed"  # BRD "Order Completed"
    STATUS_CLOSED = "Closed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Order Completed"),
        (STATUS_CLOSED, "Closed"),
    ]

    so_number = models.CharField(max_length=50)
    so_date = models.DateField()
    so_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_DOMESTIC)
    quotation = models.ForeignKey(
        Quotation, on_delete=models.SET_NULL, null=True, blank=True, related_name="sale_orders"
    )
    customer_po_no = models.CharField(max_length=50, blank=True, default="")
    customer_po_date = models.DateField(null=True, blank=True)
    valid_upto = models.DateField(null=True, blank=True)
    currency_code = models.CharField(max_length=10, default="INR")
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    closed_reason = models.CharField(max_length=255, blank=True, default="")

    # Amendment (BRD row action): same SO number, revision counter + date.
    revision_no = models.PositiveSmallIntegerField(default=0)
    amendment_date = models.DateField(null=True, blank=True)

    # Advance taken against the order. Captured only - no "paid" flag on an
    # order; it is adjusted on the invoice. (GST on advances for goods is
    # not payable - Notification 66/2017-CT.)
    advance_received = _money()
    advance_mode = models.CharField(max_length=30, blank=True, default="")

    class Meta:
        db_table = "sales_sale_order"
        ordering = ["-so_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "so_number"], name="uniq_so_number_per_tenant")]

    @property
    def display_number(self) -> str:
        return f"{self.so_number} Rev {self.revision_no}" if self.revision_no else self.so_number

    @property
    def advance_balance(self) -> Decimal:
        """Advance not yet set off against this order's invoices."""
        adjusted = (
            self.invoices.exclude(status=SalesInvoice.STATUS_CANCELLED).aggregate(total=Sum("advance_adjusted"))[
                "total"
            ]
            or ZERO
        )
        return self.advance_received - adjusted


class SaleOrderItem(PricedLine):
    sale_order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name="items")
    # Manufacturing-industry fields (BRD 6.4.3.2 3.1).
    amortisation_cost = _money()
    drawing_no = models.CharField(max_length=50, blank=True, default="")
    drawing_revision = models.CharField(max_length=20, blank=True, default="")
    line_no = models.CharField(max_length=20, blank=True, default="")
    # BRD "close the each item in Sale order": the unfulfilled balance that
    # was written off, so it stops showing as pending.
    short_closed_qty = _qty(default=ZERO)

    class Meta(PricedLine.Meta):
        db_table = "sales_sale_order_item"

    @property
    def delivered_qty(self) -> Decimal:
        agg = self.dc_lines.exclude(delivery_challan__status=DeliveryChallan.STATUS_CANCELLED).aggregate(
            total=Sum("quantity")
        )
        return agg["total"] or ZERO

    @property
    def directly_invoiced_qty(self) -> Decimal:
        """Invoiced straight from the order (no challan)."""
        agg = (
            self.invoice_lines.filter(dc_item__isnull=True)
            .exclude(sales_invoice__status=SalesInvoice.STATUS_CANCELLED)
            .exclude(sales_invoice__invoice_type=SalesInvoice.TYPE_PROFORMA)
            .aggregate(total=Sum("quantity"))
        )
        return agg["total"] or ZERO

    @property
    def fulfilled_qty(self) -> Decimal:
        return self.delivered_qty + self.directly_invoiced_qty

    @property
    def pending_qty(self) -> Decimal:
        return self.quantity - self.fulfilled_qty - self.short_closed_qty

    @property
    def invoiced_qty(self) -> Decimal:
        via_challans = (
            SalesInvoiceItem.objects.filter(dc_item__so_item=self)
            .exclude(sales_invoice__status=SalesInvoice.STATUS_CANCELLED)
            .aggregate(total=Sum("quantity"))["total"]
            or ZERO
        )
        return self.directly_invoiced_qty + via_challans


class SaleOrderRevision(models.Model):
    """Snapshot of the order as it was before an amendment."""

    sale_order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name="revisions")
    revision_no = models.PositiveSmallIntegerField()
    amendment_date = models.DateField()
    reason = models.CharField(max_length=255, blank=True, default="")
    snapshot = models.JSONField()
    created_by = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_sale_order_revision"
        ordering = ["-revision_no"]


# ---------------------------------------------------------------------------
# Delivery Challan (BRD 6.4.4, GST Rule 55)
# ---------------------------------------------------------------------------


class DeliveryChallan(models.Model):
    TYPE_ORDER = "Order"
    TYPE_DIRECT = "Direct"
    TYPE_NON_BILLABLE = "NonBillable"
    TYPE_CHOICES = [
        (TYPE_ORDER, "Order based"),
        (TYPE_DIRECT, "Direct"),
        (TYPE_NON_BILLABLE, "Non billable"),
    ]

    # Why goods move without an invoice (Rule 55). Supply = will be invoiced.
    PURPOSE_SUPPLY = "Supply"
    PURPOSE_JOB_WORK = "JobWork"
    PURPOSE_APPROVAL = "SaleOnApproval"
    PURPOSE_REPAIR = "Repair"
    PURPOSE_SAMPLE = "Sample"
    PURPOSE_OTHER = "Other"
    PURPOSE_CHOICES = [
        (PURPOSE_SUPPLY, "Supply (to be invoiced)"),
        (PURPOSE_JOB_WORK, "Job work"),
        (PURPOSE_APPROVAL, "Sale on approval"),
        (PURPOSE_REPAIR, "Repair / rework"),
        (PURPOSE_SAMPLE, "Sample / exhibition"),
        (PURPOSE_OTHER, "Other"),
    ]

    # Job-work return window (CGST Act s.143): inputs 1 year, capital goods 3.
    GOODS_INPUTS = "Inputs"
    GOODS_CAPITAL = "CapitalGoods"
    GOODS_CHOICES = [(GOODS_INPUTS, "Inputs"), (GOODS_CAPITAL, "Capital goods")]

    STATUS_OPEN = "Open"
    STATUS_CLOSED = "Closed"
    STATUS_CANCELLED = "Cancelled"
    STATUS_CHOICES = [(STATUS_OPEN, "Open"), (STATUS_CLOSED, "Closed"), (STATUS_CANCELLED, "Cancelled")]

    NATURE_SALES = "Sales"
    NATURE_LABOUR = "Labour"
    NATURE_CHOICES = [(NATURE_SALES, "Sales"), (NATURE_LABOUR, "Labour")]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="+")
    dc_number = models.CharField(max_length=50)
    dc_date = models.DateField()
    dc_type = models.CharField(max_length=15, choices=TYPE_CHOICES, default=TYPE_DIRECT)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_SUPPLY)
    tax_nature = models.CharField(max_length=10, choices=NATURE_CHOICES, default=NATURE_SALES)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    sale_order = models.ForeignKey(
        SaleOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="delivery_challans"
    )

    customer_po_no = models.CharField(max_length=50, blank=True, default="")
    customer_po_date = models.DateField(null=True, blank=True)
    kind_attn = models.CharField(max_length=100, blank=True, default="")
    to_place = models.CharField(max_length=100, blank=True, default="")
    location = models.CharField(max_length=100, blank=True, default="")
    transport_mode = models.CharField(max_length=20, blank=True, default="")
    vehicle_no = models.CharField(max_length=20, blank=True, default="")
    # The customer's own DC reference when they sent material in (job work
    # received / rework) - legacy "Inward DC No / Date".
    inward_dc_no = models.CharField(max_length=50, blank=True, default="")
    inward_dc_date = models.DateField(null=True, blank=True)
    # Legacy "App value": approximate value of the goods - Rule 55 requires
    # the challan to state a value even when no invoice is raised.
    approx_value = _money()

    returnable = models.BooleanField(default=False)
    goods_category = models.CharField(max_length=15, choices=GOODS_CHOICES, default=GOODS_INPUTS)
    return_due_date = models.DateField(null=True, blank=True)

    ship_to_name = models.CharField(max_length=150, blank=True, default="")
    ship_to_address1 = models.CharField(max_length=150, blank=True, default="")
    ship_to_address2 = models.CharField(max_length=150, blank=True, default="")
    ship_to_city = models.CharField(max_length=50, blank=True, default="")
    ship_to_state = models.CharField(max_length=50, blank=True, default="")
    ship_to_pincode = models.CharField(max_length=10, blank=True, default="")
    ship_to_gstin = models.CharField(max_length=15, blank=True, default="")

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    cancel_reason = models.CharField(max_length=255, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    created_by = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_delivery_challan"
        ordering = ["-dc_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "dc_number"], name="uniq_dc_number_per_tenant")]

    @property
    def is_billable(self) -> bool:
        return self.dc_type != self.TYPE_NON_BILLABLE


class DeliveryChallanItem(models.Model):
    delivery_challan = models.ForeignKey(DeliveryChallan, on_delete=models.CASCADE, related_name="items")
    so_item = models.ForeignKey(SaleOrderItem, on_delete=models.PROTECT, null=True, blank=True, related_name="dc_lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    uom = models.CharField(max_length=50, blank=True, default="")
    hsn_sac = models.CharField(max_length=20, blank=True, default="")
    quantity = _qty()
    rejection_qty = _qty(default=ZERO)
    rate = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    godown = models.ForeignKey(Godown, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    remarks = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "sales_delivery_challan_item"
        ordering = ["id"]

    @property
    def amount(self) -> Decimal:
        return (self.quantity * self.rate).quantize(Decimal("0.01"))

    @property
    def invoiced_qty(self) -> Decimal:
        agg = self.invoice_lines.exclude(sales_invoice__status=SalesInvoice.STATUS_CANCELLED).aggregate(
            total=Sum("quantity")
        )
        return agg["total"] or ZERO

    @property
    def pending_invoice_qty(self) -> Decimal:
        return self.quantity - self.invoiced_qty

    @property
    def returned_qty(self) -> Decimal:
        return self.return_lines.aggregate(total=Sum("quantity"))["total"] or ZERO


class ChallanReturn(models.Model):
    """Goods received back against a returnable challan (job work, repair,
    approval). Puts the stock back into a godown and closes the s.143 clock
    for that quantity."""

    delivery_challan = models.ForeignKey(DeliveryChallan, on_delete=models.PROTECT, related_name="returns")
    return_date = models.DateField()
    party_dc_no = models.CharField(max_length=50, blank=True, default="")
    remarks = models.CharField(max_length=255, blank=True, default="")
    created_by = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_challan_return"
        ordering = ["-return_date", "-id"]


class ChallanReturnItem(models.Model):
    challan_return = models.ForeignKey(ChallanReturn, on_delete=models.CASCADE, related_name="items")
    dc_item = models.ForeignKey(DeliveryChallanItem, on_delete=models.PROTECT, related_name="return_lines")
    quantity = _qty()
    godown = models.ForeignKey(Godown, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        db_table = "sales_challan_return_item"
        ordering = ["id"]


# ---------------------------------------------------------------------------
# Sales Invoice (BRD 6.4.5)
# ---------------------------------------------------------------------------


class SalesInvoice(PricedDocument, EInvoiceFields):
    TYPE_ORDER = "Order"
    TYPE_DC = "DC"
    TYPE_DIRECT = "Direct"
    TYPE_EXPORT = "Export"
    TYPE_PROFORMA = "Proforma"
    TYPE_CHOICES = [
        (TYPE_ORDER, "Order based"),
        (TYPE_DC, "Delivery challan based"),
        (TYPE_DIRECT, "Direct"),
        (TYPE_EXPORT, "Export"),
        (TYPE_PROFORMA, "Proforma"),
    ]

    # Legacy invoice sub-types (Doc_Number_Format Invoice/…/DocSubType). Used
    # as the numbering sub-type so each can keep its own series.
    NATURE_SALES = "Sales"
    NATURE_LABOUR = "Labour"
    NATURE_SCRAP = "Scrap"
    NATURE_REJECTION = "Rejection"
    NATURE_RM_SALES = "RMSales"
    NATURE_CHOICES = [
        (NATURE_SALES, "Sales"),
        (NATURE_LABOUR, "Labour"),
        (NATURE_SCRAP, "Scrap"),
        (NATURE_REJECTION, "Rejection"),
        (NATURE_RM_SALES, "Raw material sales"),
    ]

    STATUS_ACTIVE = "Active"
    STATUS_CANCELLED = "Cancelled"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_CANCELLED, "Cancelled")]

    invoice_number = models.CharField(max_length=50)
    invoice_date = models.DateField()
    invoice_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_DIRECT)
    tax_nature = models.CharField(max_length=10, choices=NATURE_CHOICES, default=NATURE_SALES)
    sale_order = models.ForeignKey(SaleOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="invoices")
    quotation = models.ForeignKey(Quotation, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    delivery_challans = models.ManyToManyField(DeliveryChallan, blank=True, related_name="invoices")

    customer_po_no = models.CharField(max_length=50, blank=True, default="")
    customer_po_date = models.DateField(null=True, blank=True)
    # The customer's PO amendment being billed against (BRD Direct Invoice
    # "Amendment No. / Date") - a reference, not a revision of this invoice.
    po_amendment_no = models.CharField(max_length=20, blank=True, default="")
    po_amendment_date = models.DateField(null=True, blank=True)
    our_dc_ref = models.CharField(max_length=50, blank=True, default="")
    your_dc_ref = models.CharField(max_length=50, blank=True, default="")
    kind_attn = models.CharField(max_length=100, blank=True, default="")

    transport_mode = models.CharField(max_length=20, blank=True, default="")
    vehicle_no = models.CharField(max_length=20, blank=True, default="")
    # Legacy "Removal Date": date & time goods were removed for supply - the
    # invoice must be issued on or before it (CGST Act s.31).
    removal_datetime = models.DateTimeField(null=True, blank=True)
    distance_km = models.PositiveIntegerField(null=True, blank=True)

    bank = models.ForeignKey(Bank, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    payment_terms = models.CharField(max_length=100, blank=True, default="")
    due_date = models.DateField(null=True, blank=True)

    ship_to_name = models.CharField(max_length=150, blank=True, default="")
    ship_to_address1 = models.CharField(max_length=150, blank=True, default="")
    ship_to_address2 = models.CharField(max_length=150, blank=True, default="")
    ship_to_city = models.CharField(max_length=50, blank=True, default="")
    ship_to_state = models.CharField(max_length=50, blank=True, default="")
    ship_to_pincode = models.CharField(max_length=10, blank=True, default="")
    ship_to_gstin = models.CharField(max_length=15, blank=True, default="")

    # Employee / counter sale reports (BRD 6.4.5.2). Text until Employee
    # Master has a backend.
    employee_code = models.CharField(max_length=30, blank=True, default="")
    employee_name = models.CharField(max_length=100, blank=True, default="")
    counter_no = models.CharField(max_length=20, blank=True, default="")

    # Export (BRD Export Invoice).
    currency_code = models.CharField(max_length=10, default="INR")
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))
    exporter_ref = models.CharField(max_length=50, blank=True, default="")
    # False = zero-rated under LUT (no IGST, "EXPWOP"); True = IGST paid.
    export_with_igst = models.BooleanField(default=False)
    lut_number = models.CharField(max_length=50, blank=True, default="")
    port_code = models.CharField(max_length=10, blank=True, default="")
    shipping_bill_no = models.CharField(max_length=30, blank=True, default="")
    shipping_bill_date = models.DateField(null=True, blank=True)
    container_no = models.CharField(max_length=50, blank=True, default="")
    goods_description = models.TextField(blank=True, default="")

    # Payment captured at billing (BRD 7. Payment Details). Receipts and
    # ledger postings belong to the Payment In / accounting module.
    advance_adjusted = _money()
    amount_received = _money()
    payment_mode = models.CharField(max_length=30, blank=True, default="")
    payment_reference = models.CharField(max_length=100, blank=True, default="")

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    cancel_reason = models.CharField(max_length=255, blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_invoice"
        ordering = ["-invoice_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "invoice_number"], name="uniq_sales_invoice_number_per_tenant")
        ]

    @property
    def balance_amount(self) -> Decimal:
        return self.net_amount - self.advance_adjusted - self.amount_received

    @property
    def is_export(self) -> bool:
        return self.invoice_type == self.TYPE_EXPORT


class SalesInvoiceItem(PricedLine):
    sales_invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="items")
    so_item = models.ForeignKey(
        SaleOrderItem, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )
    dc_item = models.ForeignKey(
        DeliveryChallanItem, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )
    godown = models.ForeignKey(Godown, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    rejection_qty = _qty(default=ZERO)
    # Promotional free item (BOGO): billed at zero, still leaves stock.
    is_free = models.BooleanField(default=False)
    remarks = models.CharField(max_length=255, blank=True, default="")

    class Meta(PricedLine.Meta):
        db_table = "sales_invoice_item"

    @property
    def returned_qty(self) -> Decimal:
        agg = self.return_lines.exclude(sales_return__status=SalesReturn.STATUS_CANCELLED).aggregate(
            total=Sum("quantity")
        )
        return agg["total"] or ZERO

    @property
    def returnable_qty(self) -> Decimal:
        return self.quantity - self.returned_qty


# ---------------------------------------------------------------------------
# Sales Return (BRD 6.4.8) and Credit Note (BRD 6.4.9)
# ---------------------------------------------------------------------------

STATUS_CANCELLED = "Cancelled"
REFUND_OR_CANCELLED_CHOICES = [*REFUND_STATUS_CHOICES, (STATUS_CANCELLED, "Cancelled")]


class RefundStatusMixin(models.Model):
    amount_refunded = _money()
    status = models.CharField(max_length=20, choices=REFUND_OR_CANCELLED_CHOICES, default=REFUND_STATUS_UNPAID)
    cancel_reason = models.CharField(max_length=255, blank=True, default="")

    STATUS_CANCELLED = STATUS_CANCELLED

    class Meta:
        abstract = True

    @property
    def balance_amount(self) -> Decimal:
        return self.net_amount - self.amount_refunded

    def refresh_status(self) -> None:
        if self.status == STATUS_CANCELLED:
            return
        self.status = refund_status_for(self.net_amount, self.amount_refunded)
        self.save(update_fields=["status", "updated_at"])


class SalesReturn(PricedDocument, RefundStatusMixin):
    return_number = models.CharField(max_length=50)
    return_date = models.DateField()
    sales_invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT, related_name="sales_returns")
    reason = models.CharField(max_length=255, blank=True, default="")
    payment_terms = models.CharField(max_length=100, blank=True, default="")
    due_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "sales_return"
        ordering = ["-return_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "return_number"], name="uniq_sales_return_number_per_tenant")
        ]


class SalesReturnItem(PricedLine):
    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="items")
    invoice_item = models.ForeignKey(SalesInvoiceItem, on_delete=models.PROTECT, related_name="return_lines")
    # Godown the goods come back into (BRD "which Godown the stock going back").
    godown = models.ForeignKey(Godown, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta(PricedLine.Meta):
        db_table = "sales_return_item"


class CreditNote(PricedDocument, RefundStatusMixin, EInvoiceFields):
    REASON_SALES_RETURN = "SalesReturn"
    REASON_POST_SALE_DISCOUNT = "PostSaleDiscount"
    REASON_PRICE_CORRECTION = "PriceCorrection"
    REASON_DEFICIENCY = "DeficiencyInService"
    REASON_OTHER = "Other"
    REASON_CHOICES = [
        (REASON_SALES_RETURN, "Sales return"),
        (REASON_POST_SALE_DISCOUNT, "Discount after sale"),
        (REASON_PRICE_CORRECTION, "Excess charged / price correction"),
        (REASON_DEFICIENCY, "Deficiency in supply"),
        (REASON_OTHER, "Other"),
    ]

    credit_note_number = models.CharField(max_length=50)
    credit_note_date = models.DateField()
    # Mandatory: a GST credit note must reference the original invoice (s.34).
    sales_invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT, related_name="credit_notes")
    sales_return = models.ForeignKey(
        SalesReturn, on_delete=models.PROTECT, null=True, blank=True, related_name="credit_notes"
    )
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default=REASON_OTHER)
    payment_terms = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "sales_credit_note"
        ordering = ["-credit_note_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "credit_note_number"], name="uniq_credit_note_number_per_tenant")
        ]


class CreditNoteItem(PricedLine):
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name="items")
    invoice_item = models.ForeignKey(
        SalesInvoiceItem, on_delete=models.PROTECT, null=True, blank=True, related_name="credit_note_lines"
    )

    class Meta(PricedLine.Meta):
        db_table = "sales_credit_note_item"


# ---------------------------------------------------------------------------
# E-Way Bill (BRD 6.4.7)
# ---------------------------------------------------------------------------


class EWayBill(models.Model):
    STATUS_ACTIVE = "Active"
    STATUS_CANCELLED = "Cancelled"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_CANCELLED, "Cancelled")]

    VEHICLE_REGULAR = "Regular"
    VEHICLE_ODC = "ODC"  # over-dimensional cargo: 1 day per 20 km
    VEHICLE_CHOICES = [(VEHICLE_REGULAR, "Regular"), (VEHICLE_ODC, "Over dimensional cargo")]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="+")
    # The invoice or delivery challan it covers.
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    document = GenericForeignKey("content_type", "object_id")
    document_number = models.CharField(max_length=50)

    ewb_number = models.CharField(max_length=20)
    ewb_date = models.DateTimeField()
    valid_till = models.DateTimeField(null=True, blank=True)
    supply_type = models.CharField(max_length=10, default="O")  # O = outward
    sub_type = models.CharField(max_length=30, blank=True, default="")
    transport_mode = models.CharField(max_length=10, default="Road")
    vehicle_type = models.CharField(max_length=10, choices=VEHICLE_CHOICES, default=VEHICLE_REGULAR)
    vehicle_no = models.CharField(max_length=20, blank=True, default="")
    transporter_id = models.CharField(max_length=15, blank=True, default="")
    transporter_name = models.CharField(max_length=100, blank=True, default="")
    transport_doc_no = models.CharField(max_length=30, blank=True, default="")
    transport_doc_date = models.DateField(null=True, blank=True)
    distance_km = models.PositiveIntegerField()
    consignment_value = _money()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    cancel_reason = models.CharField(max_length=255, blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Each vehicle change (Part B update) is kept for the audit trail.
    vehicle_history = models.JSONField(default=list, blank=True)
    created_by = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_eway_bill"
        ordering = ["-ewb_date", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]
