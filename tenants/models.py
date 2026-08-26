# tenants/models.py

import uuid

from django.db import models


class Tenant(models.Model):
    # one-line: UUID, not bigint — this ID gets referenced externally
    # (subdomains, API tokens), where non-guessable IDs are a real security
    # property. See the earlier reasoning: bigint everywhere EXCEPT tenant_id.
    tenant_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subdomain = models.CharField(max_length=63, unique=True)
    tenant_name = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant"

    def __str__(self):
        return self.tenant_name

class DocNumberSequence(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="doc_sequences")
    doc_type = models.CharField(max_length=20)  # "PARTY" now; "PURCHASE_REQUEST"/"PURCHASE_ORDER" later
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "doc_number_sequence"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "doc_type"], name="uniq_tenant_doctype_sequence")
        ]
