# tax/models.py

from django.db import models

from tenants.models import Tenant


class Tax(models.Model):
    id = models.BigAutoField(primary_key=True)  # one-line: this IS taxCode — no separate column needed,
    #exposed via the serializer
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="taxes")

    tax_type = models.CharField(max_length=20, default="GST")
    tax_name = models.CharField(max_length=50)
    cgst_percentage = models.FloatField(null=True, blank=True)
    sgst_percentage = models.FloatField(null=True, blank=True)
    igst_percentage = models.FloatField(null=True, blank=True)
    cess_percentage = models.FloatField(null=True, blank=True)
    vat_percentage = models.FloatField(null=True, blank=True)
    sales_tax_percentage = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, default="Active")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tax_master"

    def __str__(self):
        return self.tax_name
