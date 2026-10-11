# approvals/mixins.py
#
# Add ApprovalActionMixin to any ModelViewSet whose model follows the
# STATUS_PENDING/... contract (see models.py) to get, for free:
#   POST   /<resource>/{id}/decision/        - approve/reject/hold one, or release a Hold (decision="Pending")
#   POST   /<resource>/bulk-decision/        - same, for many ids at once (PO/PR approval lists)
#   GET    /<resource>/{id}/approval-history/
#
# This is how PR Approval and PO Approval end up sharing one implementation
# instead of two separate approve/reject screens with their own logic.

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .serializers import (
    ApprovalDecisionRequestSerializer,
    ApprovalHistorySerializer,
    BulkApprovalDecisionRequestSerializer,
)
from .services import get_history, record_decision


def _can_approve(request) -> bool:
    # TODO: wire to the RBAC screen/role matrix once User Master + roles are
    # built (architecture doc section 3.3 / 10.4 - "Super Admin/Admin/
    # Manager can approve PR/PO", per your filed matrix, not hardcoded
    # here). There is no login/auth in the project yet (every request is
    # AnonymousUser), so the is_authenticated check below would block
    # everyone - temporarily allowing all requests through until auth +
    # roles exist. Put the is_authenticated (and later, role) check back
    # once login is wired up:
    #     return bool(request.user and request.user.is_authenticated)
    return True


class ApprovalActionMixin:
    @action(detail=True, methods=["post"], url_path="decision")
    def decision(self, request, pk=None):
        if not _can_approve(request):
            return Response({"detail": "Not authorized to approve this document."}, status=status.HTTP_403_FORBIDDEN)

        payload = ApprovalDecisionRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        document = self.get_object()
        try:
            record_decision(
                document,
                decision=payload.validated_data["decision"],
                approved_by=payload.validated_data["approved_by"],
                reason=payload.validated_data["reason"],
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        return Response(self.get_serializer(document).data)

    @action(detail=False, methods=["post"], url_path="bulk-decision")
    def bulk_decision(self, request):
        if not _can_approve(request):
            return Response({"detail": "Not authorized to approve these documents."}, status=status.HTTP_403_FORBIDDEN)

        payload = BulkApprovalDecisionRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        approved_by = payload.validated_data["approved_by"]
        results = []
        errors = []
        queryset = self.get_queryset().filter(id__in=payload.validated_data["ids"])
        for document in queryset:
            try:
                record_decision(
                    document,
                    decision=payload.validated_data["decision"],
                    approved_by=approved_by,
                    reason=payload.validated_data["reason"],
                )
                results.append(document.id)
            except ValueError as exc:
                errors.append({"id": document.id, "error": str(exc)})

        return Response({"succeeded": results, "failed": errors})

    @action(detail=True, methods=["get"], url_path="approval-history")
    def approval_history(self, request, pk=None):
        document = self.get_object()
        return Response(ApprovalHistorySerializer(get_history(document), many=True).data)
