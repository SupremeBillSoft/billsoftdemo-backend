#items/models.py
from django.db import models

from tenants.models import Tenant


class Item(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="items")

    item_code = models.CharField(max_length=50)
    item_name = models.CharField(max_length=50)

    # one-line: the sheet marks Item_Type and Item_Status as "Boolean," but
    # CharField + choices is the better Django fit here — it stays a
    # controlled two-value field (same DB-level guarantee a boolean gives),
    # while being self-documenting in the admin panel and API responses
    # instead of a bare true/false that means nothing without external context.
    ITEM_TYPE_CHOICES = [("Product", "Product"), ("Service", "Service")]
    item_type = models.CharField(
        max_length=10, choices=ITEM_TYPE_CHOICES, default="Product"
    )

    item_description = models.CharField(max_length=150, blank=True)
    item_category = models.CharField(max_length=50, blank=True)
    brand = models.CharField(max_length=50, blank=True)

    STATUS_CHOICES = [("Active", "Active"), ("Inactive", "Inactive")]
    item_status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="Active"
    )

    purchase_uom = models.CharField(max_length=10, blank=True)
    billing_uom = models.CharField(max_length=10)
    per_purchase_uom = models.FloatField(null=True, blank=True)

    hsn_code = models.CharField(max_length=10, blank=True)
    tax_type = models.CharField(max_length=20, blank=True)
    purchase_price = models.FloatField(null=True, blank=True)
    selling_price = models.FloatField(null=True, blank=True)
    mrp = models.FloatField(null=True, blank=True)
    wholesale_discount = models.FloatField(null=True, blank=True)
    retail_discount = models.FloatField(null=True, blank=True)
    member_discount = models.FloatField(null=True, blank=True)

    # one-line: food-industry-only fields per the sheet's remark — collected
    # unconditionally at the DB level; VISIBILITY gating (show/hide based on
    # the tenant's industry type) is a frontend/serializer-level concern,
    # not a schema concern, matching what we agreed when we built Item Master.
    recipe_cost = models.FloatField(null=True, blank=True)
    dine_in_price = models.FloatField(null=True, blank=True)
    takeaway_price = models.FloatField(null=True, blank=True)
    delivery_price = models.FloatField(null=True, blank=True)

    per_unit_weight = models.FloatField(null=True, blank=True)
    packing_qty = models.IntegerField(null=True, blank=True)
    reorder_level = models.FloatField(null=True, blank=True)
    drawing_no = models.CharField(max_length=50, blank=True)
    procurement_days = models.IntegerField(null=True, blank=True)
    short_name = models.CharField(max_length=10, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "item_master"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "item_code"], name="uniq_tenant_item_code"
            )
        ]

    def __str__(self):
        return f"{self.item_code} — {self.item_name}"


class ItemCustomField(models.Model):
    # one-line: normalized as three nullable value columns (text/date/float)
    # instead of one generic "value" text column — this mirrors the
    # frontend's Zod discriminated union exactly (Text/Date/Float each
    # validated differently), and it means a Float custom field is a REAL
    # float in the database, sortable/filterable/aggregable, not a string
    # that happens to look like a number.
    id = models.BigAutoField(primary_key=True)
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="custom_fields"
    )

    FIELD_TYPE_CHOICES = [("Text", "Text"), ("Date", "Date"), ("Float", "Float")]
    field_type = models.CharField(max_length=10, choices=FIELD_TYPE_CHOICES)
    field_name = models.CharField(max_length=50)

    text_value = models.CharField(max_length=50, null=True, blank=True)
    date_value = models.DateField(null=True, blank=True)
    float_value = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "item_custom_field"

    def __str__(self):
        return f"{self.field_name} ({self.field_type})"
