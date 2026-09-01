# branch/models.py
from django.db import models

from tenants.models import Tenant


class Branch(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="branches")

    branch_code = models.CharField(max_length=30)
    branch_name = models.CharField(max_length=50)
    branch_type = models.CharField(max_length=50, blank=True)
    registration_no = models.CharField(max_length=50, blank=True)
    gstin = models.CharField(max_length=50, blank=True)
    pan = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=10, default="Active")
    remarks = models.CharField(max_length=50, blank=True)

    address_line_1 = models.CharField(max_length=50)
    address_line_2 = models.CharField(max_length=50, blank=True)
    country = models.CharField(max_length=50)
    state = models.CharField(max_length=50)
    city = models.CharField(max_length=50)
    area_locality = models.CharField(max_length=50, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    mobile_no = models.CharField(max_length=15, blank=True)
    email = models.EmailField(max_length=50, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "branch_master"
        constraints = [models.UniqueConstraint(fields=["tenant", "branch_code"], name="uniq_tenant_branch_code")]

    def __str__(self):
        return f"{self.branch_code} — {self.branch_name}"

class BranchType(models.Model):
    id = models.BigAutoField(primary_key=True)
    # one-line: nullable tenant, same pattern as PartySubTypeMaster — NULL =
    # system-wide default (Head Office/Warehouse/Branch/Others), a real
    # value = one tenant's own custom addition.
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name="branch_types")
    name = models.CharField(max_length=50)

    class Meta:
        db_table = "branch_type_master"
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_tenant_branch_type_name")]

    def __str__(self):
        return self.name
