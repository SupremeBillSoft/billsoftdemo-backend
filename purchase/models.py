# purchase/models.py - CONSOLIDATED (PR + PO + Purchase Invoice + Purchase
# Return + Debit Note, in dependency order). Adjust the `items.models.Item`
# import if your items app/model is named differently - everything else
# should be usable as-is once that one import resolves.

from decimal import Decimal

from django.db import models

from inventory.models import Godown
from items.models import Item  # adjust if the items app's model name differs
from parties.models import Party
from tax.models import Tax
from tenants.models import Tenant

# ---------------------------------------------------------------------------
# Refund status (BRD 6.5.4 / 6.5.5) - shared by Purchase Return and Debit
# Note. These are PAYMENT statuses, not an approval workflow: they are
# derived from amount_refunded vs net_amount every time the document is
# saved, never set directly by the client.
# ---------------------------------------------------------------------------

REFUND_STATUS_UNPAID = "Unpaid"
REFUND_STATUS_PARTIAL = "Partially Refunded"
REFUND_STATUS_REFUNDED = "Refunded"
REFUND_STATUS_CHOICES = [
    (REFUND_STATUS_UNPAID, "Unpaid"),
    (REFUND_STATUS_PARTIAL, "Partially Refunded"),
    (REFUND_STATUS_REFUNDED, "Refunded"),
]


def refund_status_for(net_amount: Decimal, amount_refunded: Decimal) -> str:
    if amount_refunded <= 0:
        return REFUND_STATUS_UNPAID
    if amount_refunded < net_amount:
        return REFUND_STATUS_PARTIAL
    return REFUND_STATUS_REFUNDED

# ---------------------------------------------------------------------------
# Purchase Requisition (architecture doc section 9.1)
# ---------------------------------------------------------------------------


class PurchaseRequisition(models.Model):
    STATUS_PENDING = "Pending"
    STATUS_APPROVED = "Approved"
    STATUS_REJECTED = "Rejected"
    STATUS_HOLD = "Hold"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_HOLD, "Hold"),
    ]

    TYPE_ITEM = "Item"
    TYPE_GENERAL = "General"
    TYPE_CHOICES = [
        (TYPE_ITEM, "Item based"),
        (TYPE_GENERAL, "General"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="purchase_requisitions")

    # Issued by docnumbering.services.next_number(tenant, doc="PR") at
    # creation - never client-supplied, never editable after.
    pr_number = models.CharField(max_length=50)
    pr_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_ITEM)

    # Optional - BRD 6.5.1.1.2: "System shall allow user to create PR
    # without Supplier name". The supplier is then chosen on the PO.
    supplier = models.ForeignKey(
        Party, on_delete=models.PROTECT, null=True, blank=True, related_name="purchase_requisitions"
    )
    raised_by = models.CharField(max_length=100, blank=True, default="")
    pr_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    approved_by = models.CharField(max_length=100, blank=True, default="")  # set by approvals.record_decision
    remarks = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "pr_number"], name="uniq_pr_number_per_tenant")
        ]
        ordering = ["-pr_date", "-id"]

    def __str__(self):
        return f"{self.pr_number} ({self.supplier or 'no supplier'})"


class PurchaseRequisitionItem(models.Model):
    purchase_requisition = models.ForeignKey(
        PurchaseRequisition, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    uom = models.CharField(max_length=50, blank=True, default="")
    hsn_sac = models.CharField(max_length=20, blank=True, default="")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    delivery_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def ordered_qty(self) -> Decimal:
        # Sum of PO lines drawn against this PR line - a Rejected PO gives
        # its quantity back, so it doesn't count.
        agg = self.po_lines.exclude(purchase_order__status=PurchaseOrder.STATUS_REJECTED).aggregate(
            total=models.Sum("order_qty")
        )
        return agg["total"] or Decimal("0")

    @property
    def pending_order_qty(self) -> Decimal:
        return self.quantity - self.ordered_qty


# ---------------------------------------------------------------------------
# Purchase Order (architecture doc section 9.2)
# ---------------------------------------------------------------------------


class PurchaseOrder(models.Model):
    STATUS_PENDING = "Pending"
    STATUS_APPROVED = "Approved"
    STATUS_REJECTED = "Rejected"
    STATUS_HOLD = "Hold"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_HOLD, "Hold"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="purchase_orders")

    # Issued by docnumbering.services.next_number(tenant, doc="PO").
    po_number = models.CharField(max_length=50)

    # No header-level PR link: per BRD 6.5.2.1.2 the PR No. lives on each
    # item line (PurchaseOrderItem.pr_item), so one PO can draw from several
    # PRs and a PR can be split across several POs. A PO with no PR lines
    # is a standalone PO.

    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_orders")
    po_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    raised_by = models.CharField(max_length=100, blank=True, default="")
    approved_by = models.CharField(max_length=100, blank=True, default="")  # set by approvals.record_decision

    # BRD 6.5.2.1.2 PO details.
    quotation_no = models.CharField(max_length=50, blank=True, default="")
    quotation_date = models.DateField(null=True, blank=True)
    validity_date = models.DateField(null=True, blank=True)
    kind_attn = models.CharField(max_length=100, blank=True, default="")
    other_reference = models.CharField(max_length=150, blank=True, default="")
    delivery_to = models.CharField(max_length=255, blank=True, default="")
    delivery = models.CharField(max_length=255, blank=True, default="")

    # Commercial fields (architecture doc section 9.2).
    terms = models.TextField(blank=True, default="")
    payment_terms = models.CharField(max_length=100, blank=True, default="")
    currency_code = models.CharField(max_length=10, blank=True, default="") # TODO: FK to Currency Master once confirmed
    dispatch_details = models.TextField(blank=True, default="")
    remarks = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "po_number"], name="uniq_po_number_per_tenant")
        ]
        ordering = ["-po_date", "-id"]

    def __str__(self):
        return f"{self.po_number} ({self.supplier})"

    @property
    def net_amount(self) -> Decimal:
        return sum((line.line_total for line in self.items.all()), Decimal("0"))


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    # The Approved PR line this PO line orders against (null = added
    # manually). Drives PurchaseRequisitionItem.pending_order_qty.
    pr_item = models.ForeignKey(
        PurchaseRequisitionItem, on_delete=models.PROTECT, null=True, blank=True, related_name="po_lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    uom = models.CharField(max_length=50, blank=True, default="")
    hsn_sac = models.CharField(max_length=20, blank=True, default="")

    order_qty = models.DecimalField(max_digits=14, decimal_places=3)
    # Tracks partial fulfillment across multiple Purchase Invoices against
    # the same PO line (architecture doc section 10.3) - incremented by
    # Purchase Invoice's creation logic, not edited directly here.
    invoiced_qty = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))

    rate = models.DecimalField(max_digits=14, decimal_places=2)
    # BRD 6.5.2.1.2 line charges. Taxable value = qty x rate - discount +
    # P&F + freight, all taxed at this line's GST rate (agreed: freight/P&F
    # billed with goods follows the goods' rate).
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    pack_forward_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    freight_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    # Tax master row this line is taxed with (BRD "Tax Type" per line) -
    # defaults from the item, user-changeable. Kept for audit.
    tax = models.ForeignKey(Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    notes = models.CharField(max_length=255, blank=True, default="")

    # GST breakdown, computed via common.gst.engine at save time and stored
    # for audit/reporting rather than recalculated on every read.
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cess = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    delivery_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def pending_qty(self) -> Decimal:
        return self.order_qty - self.invoiced_qty


# ---------------------------------------------------------------------------
# Purchase Invoice (architecture doc section 9.3 / 10.3)
#
# DESIGN NOTE: no approval workflow of its own - gated upstream (only an
# Approved PO can be invoiced). "Cancel" (see views.py) is the normal
# teardown path, not Delete.
# ---------------------------------------------------------------------------


class PurchaseInvoice(models.Model):
    TYPE_PO = "PO"
    TYPE_FREIGHT = "Freight"
    TYPE_CHOICES = [
        (TYPE_PO, "Purchase Order Based"),
        (TYPE_FREIGHT, "Freight (standalone)"),
    ]

    STATUS_ACTIVE = "Active"
    STATUS_CANCELLED = "Cancelled"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="purchase_invoices")

    # Issued by docnumbering.services.next_number(tenant, doc="PI") - add a
    # matching Doc Number Format row for code "PI".
    invoice_number = models.CharField(max_length=50)

    # PO-based invoices (the normal path) are sourced from an Approved PO.
    # A standalone Freight invoice has no PO: its lines are Service items
    # taxed at their own GST rate (agreed: freight billed on its own is a
    # service).
    invoice_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_PO)
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="purchase_invoices"
    )
    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_invoices")

    invoice_date = models.DateField()
    # The supplier's own invoice reference - what matters for GST input-
    # credit matching and vendor reconciliation, distinct from invoice_number.
    supplier_invoice_no = models.CharField(max_length=100)
    supplier_invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=100, blank=True, default="")
    inward_no = models.CharField(max_length=50, blank=True, default="")
    inward_date = models.DateField(null=True, blank=True)
    purchase_type = models.CharField(max_length=50, blank=True, default="")  # plain text until Purchase Head Master

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    remarks = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "invoice_number"], name="uniq_pi_number_per_tenant"),
            # GST compliance: the same supplier-invoice reference shouldn't
            # be booked twice for one supplier within a tenant.
            models.UniqueConstraint(
                fields=["tenant", "supplier", "supplier_invoice_no"], name="uniq_supplier_invoice_no_per_supplier"
            ),
        ]
        ordering = ["-invoice_date", "-id"]

    def __str__(self):
        return f"{self.invoice_number} ({self.supplier})"

    @property
    def net_amount(self) -> Decimal:
        return sum((line.line_total for line in self.items.all()), Decimal("0"))


class PurchaseInvoiceItem(models.Model):
    purchase_invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE, related_name="items")
    # The specific PO line this invoice line is drawing against - lets
    # pending_qty be tracked accurately across several invoices against the
    # same PO.
    po_item = models.ForeignKey(
        PurchaseOrderItem, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    uom = models.CharField(max_length=50, blank=True, default="")
    hsn_sac = models.CharField(max_length=20, blank=True, default="")

    invoiced_qty = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    # BRD 6.5.2.1.2 line charges. Taxable value = qty x rate - discount +
    # P&F + freight, all taxed at this line's GST rate (agreed: freight/P&F
    # billed with goods follows the goods' rate).
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    pack_forward_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    freight_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    # Tax master row this line is taxed with (BRD "Tax Type" per line) -
    # defaults from the item, user-changeable. Kept for audit.
    tax = models.ForeignKey(Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    remarks = models.CharField(max_length=255, blank=True, default="")

    # Godown the goods are received into (BRD 6.5.3.2 "selected godown
    # details shall be linked to the invoice item"). Required for Product
    # lines; Services aren't stocked.
    godown = models.ForeignKey(Godown, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    # Alternate unit (BRD 6.5.3.2, updated 20/Jul): equivalent qty in the
    # secondary unit = invoiced_qty x conversion_rate.
    secondary_uom = models.CharField(max_length=50, blank=True, default="")
    conversion_rate = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    # GST breakdown, computed via common.gst.engine at save time (the rate
    # CAN differ from the PO's rate if the supplier billed differently -
    # exactly the kind of discrepancy a 3-way match / Debit Note catches).
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cess = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["id"]

    @property
    def returned_qty(self) -> Decimal:
        # Returns have no approval step (BRD 6.5.4), so every saved return
        # line reserves its quantity immediately.
        agg = self.return_lines.aggregate(total=models.Sum("returned_qty"))
        return agg["total"] or Decimal("0")

    @property
    def returnable_qty(self) -> Decimal:
        return self.invoiced_qty - self.returned_qty

    @property
    def secondary_qty(self) -> Decimal | None:
        if not self.conversion_rate:
            return None
        return self.invoiced_qty * self.conversion_rate


# ---------------------------------------------------------------------------
# Purchase Return (architecture doc section 9.4)
#
# DESIGN NOTE: no approval workflow - status is the refund status
# (Unpaid / Partially Refunded / Refunded), derived from amount_refunded.
# Returned quantity is tracked WITHOUT a stored counter (see
# PurchaseInvoiceItem.returned_qty above).
# ---------------------------------------------------------------------------


class PurchaseReturn(models.Model):

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="purchase_returns")

    # Issued by docnumbering.services.next_number(tenant, doc="PRET") - add a
    # matching Doc Number Format row for code "PRET".
    return_number = models.CharField(max_length=50)

    # Always sourced from a Purchase Invoice - you can only return what was
    # actually billed and received, not raw PO lines that haven't been
    # invoiced yet.
    purchase_invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.PROTECT, related_name="purchase_returns")
    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_returns")

    return_date = models.DateField()
    reason = models.CharField(max_length=255, blank=True, default="")
    amount_refunded = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=20, choices=REFUND_STATUS_CHOICES, default=REFUND_STATUS_UNPAID)
    remarks = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "return_number"], name="uniq_pret_number_per_tenant")
        ]
        ordering = ["-return_date", "-id"]

    def __str__(self):
        return f"{self.return_number} ({self.supplier})"

    @property
    def net_amount(self) -> Decimal:
        return sum((line.line_total for line in self.items.all()), Decimal("0"))

    @property
    def balance_amount(self) -> Decimal:
        return self.net_amount - self.amount_refunded

    def refresh_status(self) -> None:
        self.status = refund_status_for(self.net_amount, self.amount_refunded)
        self.save(update_fields=["status", "updated_at"])


class PurchaseReturnItem(models.Model):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="items")
    # The specific invoice line this return is drawn against - lets
    # returnable_qty be computed accurately even across several partial
    # returns against the same invoice line.
    invoice_item = models.ForeignKey(PurchaseInvoiceItem, on_delete=models.PROTECT, related_name="return_lines")

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    uom = models.CharField(max_length=50, blank=True, default="")
    hsn_sac = models.CharField(max_length=20, blank=True, default="")

    returned_qty = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    # Tax master row this line is taxed with (BRD "Tax Type" per line) -
    # defaults from the item, user-changeable. Kept for audit.
    tax = models.ForeignKey(Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # Godown the goods go back out of (BRD 6.5.4.2 "+ Reduce ... which
    # Godown the stock going back"). Required for Product lines.
    godown = models.ForeignKey(Godown, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    # GST breakdown mirrors the original invoice line's tax treatment -
    # computed via common.gst.engine at save time, same as everywhere else.
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cess = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["id"]


# ---------------------------------------------------------------------------
# Debit Note (architecture doc section 9.5)
#
# DESIGN NOTE: linked to a Purchase Invoice (BRD 6.5.5.2 "Link to
# Invoice"), or standalone when the invoice is left blank. No approval
# workflow - status is the refund status, plus Cancelled via the Cancel
# row action.
# ---------------------------------------------------------------------------


class DebitNote(models.Model):
    STATUS_CANCELLED = "Cancelled"
    STATUS_CHOICES = [*REFUND_STATUS_CHOICES, (STATUS_CANCELLED, "Cancelled")]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="debit_notes")

    # Issued by docnumbering.services.next_number(tenant, doc="DN") - add a
    # matching Doc Number Format row for code "DN".
    debit_note_number = models.CharField(max_length=50)

    # Nullable - supports both creation paths (see class docstring above).
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.PROTECT, null=True, blank=True, related_name="debit_notes"
    )
    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="debit_notes")

    debit_note_date = models.DateField()
    reason = models.CharField(max_length=255, blank=True, default="")
    amount_refunded = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=REFUND_STATUS_UNPAID)
    remarks = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "debit_note_number"], name="uniq_dn_number_per_tenant")
        ]
        ordering = ["-debit_note_date", "-id"]

    def __str__(self):
        return f"{self.debit_note_number} ({self.supplier})"

    @property
    def net_amount(self) -> Decimal:
        return sum((line.line_total for line in self.items.all()), Decimal("0"))

    @property
    def balance_amount(self) -> Decimal:
        return self.net_amount - self.amount_refunded

    def refresh_status(self) -> None:
        # A Cancelled note stays Cancelled - only the refund statuses move.
        if self.status == self.STATUS_CANCELLED:
            return
        self.status = refund_status_for(self.net_amount, self.amount_refunded)
        self.save(update_fields=["status", "updated_at"])


class DebitNoteItem(models.Model):
    debit_note = models.ForeignKey(DebitNote, on_delete=models.CASCADE, related_name="items")
    # The invoice line this was auto-fetched from (null for manually added
    # rows or a standalone Debit Note).
    invoice_item = models.ForeignKey(
        PurchaseInvoiceItem, on_delete=models.PROTECT, null=True, blank=True, related_name="debit_note_lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True, default="")
    uom = models.CharField(max_length=50, blank=True, default="")
    hsn_sac = models.CharField(max_length=20, blank=True, default="")

    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    # Tax master row this line is taxed with (BRD "Tax Type" per line) -
    # defaults from the item, user-changeable. Kept for audit.
    tax = models.ForeignKey(Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    cess = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["id"]
