# hsn/models.py
from django.db import models

from tenants.models import Tenant


class HSN(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="hsns")

    hsn_code = models.CharField(max_length=8)
    hsn_name = models.CharField(max_length=100)
    item_type = models.CharField(max_length=50, blank=True)
    product_category = models.CharField(max_length=50, blank=True)
    tax_type = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=10, default="Active")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hsn_master"
        constraints = [models.UniqueConstraint(fields=["tenant", "hsn_code"], name="uniq_tenant_hsn_code")]

    def __str__(self):
        return f"{self.hsn_code} — {self.hsn_name}"


class HSNItemType(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name="hsn_item_types")
    name = models.CharField(max_length=50)

    class Meta:
        db_table = "hsn_item_type_master"
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_tenant_hsn_item_type_name")]

    def __str__(self):
        return self.name


class HSNProductCategory(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True,
                               related_name="hsn_product_categories")
    name = models.CharField(max_length=50)

    class Meta:
        db_table = "hsn_product_category_master"
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_tenant_hsn_product_category_name")]

    def __str__(self):
        return self.name
