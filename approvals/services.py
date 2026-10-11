# approvals/services.py

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import ApprovalHistory


@transaction.atomic
def record_decision(document, decision: str, approved_by: str, reason: str):
    """The one function every approval action ultimately calls, whatever
    document type it's for. Moves `document.status` and writes an audit
    row - PR Approval and PO Approval both call this via
    ApprovalActionMixin (mixins.py), so they can never drift apart.

    Approve/Reject/Hold only from Pending; "Pending" (release) only from
    Hold. Both "approved by" and a reason are required (BRD 6.5.1.2 / 6.5.2.2
    - "User should provide below details before Approve/Reject/Hold")."""
    valid_decisions = dict(ApprovalHistory.DECISION_CHOICES)
    if decision not in valid_decisions:
        raise ValueError(f"Invalid decision '{decision}' - must be one of {list(valid_decisions)}.")
    if not (approved_by or "").strip():
        raise ValueError("'Approved by' is required.")
    if not (reason or "").strip():
        raise ValueError("A reason is required.")

    if decision == ApprovalHistory.DECISION_PENDING:
        if document.status != document.STATUS_HOLD:
            raise ValueError(f"Only a {document.STATUS_HOLD} document can be released back to Pending "
                             f"(current status: {document.status}).")
    elif document.status != document.STATUS_PENDING:
        raise ValueError(
            f"Only a {document.STATUS_PENDING} document can be approved/rejected/held "
            f"(current status: {document.status})."
        )

    document.status = decision
    update_fields = ["status", "updated_at"]
    if hasattr(document, "approved_by"):
        # Shown as "Approved By" (or Rejected/Held by) on the list screens;
        # cleared when released back to Pending.
        document.approved_by = "" if decision == ApprovalHistory.DECISION_PENDING else approved_by.strip()
        update_fields.append("approved_by")
    document.save(update_fields=update_fields)

    ApprovalHistory.objects.create(
        tenant=document.tenant,
        content_type=ContentType.objects.get_for_model(document),
        object_id=document.id,
        decision=decision,
        approved_by=approved_by.strip(),
        reason=reason.strip(),
    )
    return document


def get_history(document) -> list[ApprovalHistory]:
    content_type = ContentType.objects.get_for_model(document)
    return list(
        ApprovalHistory.objects.filter(content_type=content_type, object_id=document.id).order_by("-created_at")
    )
