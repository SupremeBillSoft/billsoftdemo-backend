# units/models.py

from django.db import models

from tenants.models import Tenant


class Unit(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="units")

    unit_code = models.CharField(max_length=30)
    unit_name = models.CharField(max_length=50)
    unit_description = models.CharField(max_length=50)
    # one-line: fixed "OTH" default per the confirmed requirement — the
    # frontend disables this field, so in practice every row saves as "OTH"
    # for now; kept as a real editable column (not hardcoded server-side)
    # since a future requirement change shouldn't need a migration.
    einvoice_uom = models.CharField(max_length=30, default="OTH")
    unit_status = models.CharField(max_length=10, default="Active")
    remarks = models.CharField(max_length=50, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "unit_master"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "unit_code"], name="uniq_tenant_unit_code")
        ]

    def __str__(self):
        return f"{self.unit_code} — {self.unit_name}"
