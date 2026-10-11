# units/serializers.py

from rest_framework import serializers

from common.validation import ensure_unique_for_tenant

from .models import Unit


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ["id", "unit_code", "unit_name", "unit_description", "einvoice_uom", "unit_status", "remarks",
                   "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_unit_code(self, value):
        if not value.isalnum():
            raise serializers.ValidationError("Unit Code must be alphanumeric")
        return ensure_unique_for_tenant(self, "unit_code", value, "Unit Code")
