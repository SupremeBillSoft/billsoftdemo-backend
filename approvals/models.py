# approvals/models.py
#
# Generic Pending/Approved/Rejected/Hold workflow, per architecture doc
# section 3.2/10.4 - one implementation, reused by PR Approval, PO Approval,
# and later Party Screen Approval, instead of three separate approval
# screens each with their own status/history logic.
#
# CONTRACT for any model that wants approvals via ApprovalActionMixin
# (see mixins.py): it must have a `status` CharField and class attributes
# STATUS_PENDING / STATUS_APPROVED / STATUS_REJECTED / STATUS_HOLD with
# those exact string values (PurchaseRequisition already does this).

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from tenants.models import Tenant


class ApprovalHistory(models.Model):
    DECISION_APPROVED = "Approved"
    DECISION_REJECTED = "Rejected"
    DECISION_HOLD = "Hold"
    # Releases a Hold document back to Pending (the only way out of Hold -
    # BRD doesn't define one yet, agreed Hold -> Pending until it does).
    DECISION_PENDING = "Pending"
    DECISION_CHOICES = [
        (DECISION_APPROVED, "Approved"),
        (DECISION_REJECTED, "Rejected"),
        (DECISION_HOLD, "Hold"),
        (DECISION_PENDING, "Released to Pending"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="approval_history")

    # Generic FK so this one table serves PR, PO, and any future approvable
    # document without a schema change.
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    document = GenericForeignKey("content_type", "object_id")

    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    approved_by = models.CharField(max_length=100, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.decision} on {self.content_type}:{self.object_id} by {self.approved_by}"
