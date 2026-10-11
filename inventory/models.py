# inventory/models.py
#
# BRD 6.6 Inventory + the Item Master "Adjust Stock" popup (6.3.1 item 7).
# Stock is never stored as a mutable counter: every change is a
# StockMovement row (signed quantity), and stock on hand is the sum of the
# movements for an (item, godown). Purchase Invoice posts +qty, Purchase
# Return posts -qty, adjustments and transfers post their own rows - so the
# full history is always reconstructible.

from decimal import Decimal

from django.db import models

from items.models import Item
from tenants.models import Tenant


class Godown(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="godowns")
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=150, blank=True, default="")
    state = models.CharField(max_length=50, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    # BRD 6.6.1 item 1 "display the godown status (e.g., Main Godown)" - at
    # most one main godown per tenant (enforced in the serializer).
    is_main = models.BooleanField(default=False)
    status = models.CharField(max_length=10, default="Active")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "godown"
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_tenant_godown_name")]
        ordering = ["-is_main", "name"]

    def __str__(self):
        return self.name


class StockMovement(models.Model):
    TYPE_PURCHASE = "Purchase"
    TYPE_PURCHASE_RETURN = "Purchase Return"
    TYPE_ADJUSTMENT_ADD = "Adjustment Add"
    TYPE_ADJUSTMENT_REDUCE = "Adjustment Reduce"
    TYPE_TRANSFER_OUT = "Transfer Out"
    TYPE_TRANSFER_IN = "Transfer In"
    TYPE_SALE = "Sale"
    TYPE_SALES_RETURN = "Sales Return"
    TYPE_DC_OUT = "Delivery Challan"
    TYPE_DC_RETURN = "Challan Return"
    TYPE_CHOICES = [
        (TYPE_PURCHASE, "Purchase"),
        (TYPE_PURCHASE_RETURN, "Purchase Return"),
        (TYPE_ADJUSTMENT_ADD, "Adjustment Add"),
        (TYPE_ADJUSTMENT_REDUCE, "Adjustment Reduce"),
        (TYPE_TRANSFER_OUT, "Transfer Out"),
        (TYPE_TRANSFER_IN, "Transfer In"),
        (TYPE_SALE, "Sale"),
        (TYPE_SALES_RETURN, "Sales Return"),
        (TYPE_DC_OUT, "Delivery Challan"),
        (TYPE_DC_RETURN, "Challan Return"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="stock_movements")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_movements")
    godown = models.ForeignKey(Godown, on_delete=models.PROTECT, related_name="stock_movements")
    movement_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    movement_date = models.DateField()
    # Signed: +ve adds stock, -ve removes it.
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    batch_no = models.CharField(max_length=50, blank=True, default="")

    # The document that caused this movement, e.g. ("PurchaseInvoice", 12,
    # "PI-0012"). Re-saving / cancelling a document replaces its rows.
    source_doc_type = models.CharField(max_length=30, blank=True, default="")
    source_doc_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_doc_number = models.CharField(max_length=50, blank=True, default="")

    # Stock in this (item, godown) just before/after this row was written -
    # what the BRD's Stock Adjustment audit history shows as Old/Updated Stock.
    balance_before = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    balance_after = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))

    remarks = models.CharField(max_length=255, blank=True, default="")
    created_by = models.CharField(max_length=100, blank=True, default="")  # see tenants.services.current_user_name
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_movement"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "item", "godown"]),
            models.Index(fields=["source_doc_type", "source_doc_id"]),
        ]


class StockTransfer(models.Model):
    """BRD 6.6.1 item 9 - moves stock between two godowns. Posts one
    Transfer Out + one Transfer In movement per line."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="stock_transfers")
    from_godown = models.ForeignKey(Godown, on_delete=models.PROTECT, related_name="transfers_out")
    to_godown = models.ForeignKey(Godown, on_delete=models.PROTECT, related_name="transfers_in")
    transfer_date = models.DateField()
    remarks = models.CharField(max_length=255, blank=True, default="")
    created_by = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_transfer"
        ordering = ["-transfer_date", "-id"]

    @property
    def transfer_number(self) -> str:
        return f"ST-{self.id:04d}"


class StockTransferItem(models.Model):
    stock_transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        db_table = "stock_transfer_item"
        ordering = ["id"]
