# purchase/models.py

from django.db import models

from parties.models import Party
from tenants.models import Tenant

APPROVAL_STATUS_CHOICES = [
    ("Pending Approval", "Pending Approval"),
    ("Approved", "Approved"),
    ("Hold", "Hold"),
    ("Rejected", "Rejected"),
]


class PurchaseRequisition(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="purchase_requisitions")

    pr_no = models.CharField(max_length=20)
    pr_type = models.CharField(max_length=20, choices=[("Item Based", "Item Based"), ("General", "General")],
                                default="Item Based")
    supplier = models.ForeignKey(Party, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="purchase_requisitions")
    raised_by = models.CharField(max_length=50)
    pr_date = models.DateField()
    format_no = models.CharField(max_length=50, blank=True)

    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default="Pending Approval")
    approved_by = models.CharField(max_length=50, blank=True)
    reason = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "purchase_requisition"
        constraints = [models.UniqueConstraint(fields=["tenant", "pr_no"], name="uniq_tenant_pr_no")]

    def __str__(self):
        return self.pr_no


class PurchaseRequisitionItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    pr = models.ForeignKey(PurchaseRequisition, on_delete=models.CASCADE, related_name="items")

    item_code = models.CharField(max_length=50)
    description = models.CharField(max_length=150, blank=True)
    uom = models.CharField(max_length=10, blank=True)
    hsn_sac = models.CharField(max_length=10, blank=True)
    qty = models.FloatField()
    delivery_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "purchase_requisition_item"


class PurchaseOrder(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="purchase_orders")

    po_no = models.CharField(max_length=20)
    supplier = models.ForeignKey(Party, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="purchase_orders")
    # one-line: nullable — supports BOTH creation paths confirmed earlier:
    # PR-driven (this is set) and fully standalone (this stays null).
    pr = models.ForeignKey(PurchaseRequisition, on_delete=models.SET_NULL, null=True, blank=True,
                            related_name="purchase_orders")

    bill_no = models.CharField(max_length=50, blank=True)
    format_no = models.CharField(max_length=50, blank=True)
    quotation_no = models.CharField(max_length=50, blank=True)
    quotation_date = models.DateField(null=True, blank=True)
    po_date = models.DateField()
    validity = models.DateField(null=True, blank=True)

    payment_terms = models.CharField(max_length=50, blank=True)
    document = models.CharField(max_length=100, blank=True)
    despatch_through = models.CharField(max_length=100, blank=True)
    currency = models.CharField(max_length=10, default="INR")
    delivery = models.CharField(max_length=100, blank=True)
    kind_attn = models.CharField(max_length=100, blank=True)
    other_ref = models.CharField(max_length=100, blank=True)
    delivery_to = models.CharField(max_length=150, blank=True)

    # one-line: server-computed totals, recalculated on every save from the
    # item rows — never trust a client-submitted total, same "never trust
    # the client" principle as everywhere else in this app.
    goods_value = models.FloatField(default=0)
    cgst_amount = models.FloatField(default=0)
    sgst_amount = models.FloatField(default=0)
    igst_amount = models.FloatField(default=0)
    tax_value = models.FloatField(default=0)
    net_value = models.FloatField(default=0)

    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default="Pending Approval")
    approved_by = models.CharField(max_length=50, blank=True)
    reason = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "purchase_order"
        constraints = [models.UniqueConstraint(fields=["tenant", "po_no"], name="uniq_tenant_po_no")]

    def __str__(self):
        return self.po_no


class PurchaseOrderItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")

    item_code = models.CharField(max_length=50)
    description = models.CharField(max_length=150, blank=True)
    qty = models.FloatField()
    uom = models.CharField(max_length=10, blank=True)
    rate = models.FloatField(default=0)
    discount_percentage = models.FloatField(default=0)
    value = models.FloatField(default=0)  # computed: qty * rate * (1 - discount/100)
    pack_forwarding = models.FloatField(default=0)
    freight_amount = models.FloatField(default=0)
    tax_type = models.CharField(max_length=50, blank=True)  # e.g. "18% GST" — the combined label from Tax Master
    tax_amount = models.FloatField(default=0)  # computed
    hsn_sac = models.CharField(max_length=10, blank=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "purchase_order_item"


class PurchaseOrderTerm(models.Model):
    id = models.BigAutoField(primary_key=True)
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="terms")
    text = models.CharField(max_length=200)

    class Meta:
        db_table = "purchase_order_term"
