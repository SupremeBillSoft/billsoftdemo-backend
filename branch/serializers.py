# branch/serializers.py
from rest_framework import serializers

from common.validation import ensure_unique_for_tenant

from .models import Branch, BranchType


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "branch_code", "branch_name", "branch_type", "registration_no", "gstin", "pan",
                  "status", "remarks", "address_line_1", "address_line_2", "country", "state", "city",
                  "area_locality", "pincode", "mobile_no", "email", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_branch_code(self, value):
        return ensure_unique_for_tenant(self, "branch_code", value, "Branch Code")


class BranchTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BranchType
        fields = ["id", "name"]
