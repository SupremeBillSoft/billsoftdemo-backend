# tenants/serializers.py

import re

from rest_framework import serializers

from .models import USER_ROLES, CompanySettings

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class CompanySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanySettings
        fields = [
            "business_name",
            "phone",
            "email",
            "address_1",
            "address_2",
            "city",
            "pincode",
            "state",
            "country",
            "gst_registered",
            "gstin",
            "pan_no",
            "financial_year_start_month",
            "allow_po_without_pr",
            "auto_approve_pr_roles",
            "auto_approve_po_roles",
            "allow_negative_stock_on_sales",
            "einvoice_enabled",
            "aato_10cr_or_more",
            "eway_bill_threshold",
            "eway_bill_intra_state_thresholds",
            "lut_number",
            "lut_valid_till",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_gstin(self, value: str) -> str:
        value = value.strip().upper()
        if value and not GSTIN_PATTERN.match(value):
            raise serializers.ValidationError("Enter a valid 15-character GSTIN.")
        return value

    def validate_pan_no(self, value: str) -> str:
        value = value.strip().upper()
        if value and not PAN_PATTERN.match(value):
            raise serializers.ValidationError("Enter a valid 10-character PAN.")
        return value

    def validate_financial_year_start_month(self, value: int) -> int:
        if not 1 <= value <= 12:
            raise serializers.ValidationError("Enter a month from 1 to 12.")
        return value

    def _validate_roles(self, value):
        if not isinstance(value, list) or any(role not in USER_ROLES for role in value):
            raise serializers.ValidationError(f"Roles must be from: {', '.join(USER_ROLES)}.")
        return list(dict.fromkeys(value))

    def validate_auto_approve_pr_roles(self, value):
        return self._validate_roles(value)

    def validate_auto_approve_po_roles(self, value):
        return self._validate_roles(value)

    def validate(self, attrs):
        gst_registered = attrs.get("gst_registered", getattr(self.instance, "gst_registered", False))
        gstin = attrs.get("gstin", getattr(self.instance, "gstin", ""))
        # BRD 6.7.1 item 7: GSTIN is captured when the business is GST registered.
        if gst_registered and not gstin:
            raise serializers.ValidationError({"gstin": "GSTIN is required for a GST registered business."})
        return attrs
