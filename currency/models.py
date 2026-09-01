# currency/models.py
from django.db import models

from tenants.models import Tenant


class Currency(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="currencies")

    currency_code = models.CharField(max_length=10)
    currency_name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=10)
    major_unit_name = models.CharField(max_length=30)
    major_unit_name_plural = models.CharField(max_length=30)
    minor_unit_name = models.CharField(max_length=30, blank=True)
    minor_unit_name_plural = models.CharField(max_length=30, blank=True)
    decimal_places = models.PositiveSmallIntegerField(default=2)
    symbol_position = models.CharField(max_length=10, default="Prefix")
    status = models.CharField(max_length=10, default="Active")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "currency_master"
        constraints = [models.UniqueConstraint(fields=["tenant", "currency_code"], name="uniq_tenant_currency_code")]

    def __str__(self):
        return f"{self.currency_code} — {self.currency_name}"
