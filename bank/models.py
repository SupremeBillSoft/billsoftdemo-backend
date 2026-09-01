# bank/models.py

from django.db import models

from tenants.models import Tenant


class Bank(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="banks")

    bank_code = models.CharField(max_length=10)  # real business code, user-entered — matches Party/Item/Unit's pattern,
                                                # not Tax's auto-ID approach
    bank_name = models.CharField(max_length=50)
    branch_name = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=150, blank=True)
    account_no = models.CharField(max_length=50)
    account_type = models.CharField(max_length=20, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    micr_code = models.CharField(max_length=20, blank=True)
    remarks = models.CharField(max_length=100, blank=True)
    opening_balance = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, default="Active")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bank_master"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "bank_code"], name="uniq_tenant_bank_code")
        ]

    def __str__(self):
        return f"{self.bank_code} — {self.bank_name}"
