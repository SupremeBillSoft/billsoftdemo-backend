# approvals/serializers.py

from rest_framework import serializers

from .models import ApprovalHistory


class ApprovalDecisionRequestSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=[c for c, _ in ApprovalHistory.DECISION_CHOICES])
    # TODO (login): typed in by the user for now; take it from the logged-in
    # user once login exists - see tenants.services.current_user_name.
    approved_by = serializers.CharField(max_length=100)
    reason = serializers.CharField()


class BulkApprovalDecisionRequestSerializer(ApprovalDecisionRequestSerializer):
    ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class ApprovalHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalHistory
        fields = ["id", "decision", "approved_by", "reason", "created_at"]
