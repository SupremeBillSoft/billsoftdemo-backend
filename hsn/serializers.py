# hsn/serializers.py
from rest_framework import serializers

from common.validation import ensure_unique_for_tenant

from .models import HSN, HSNItemType, HSNProductCategory


class HSNSerializer(serializers.ModelSerializer):
    class Meta:
        model = HSN
        fields = ["id", "hsn_code", "hsn_name", "item_type", "product_category", "tax_type", "status",
                   "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_hsn_code(self, value):
        return ensure_unique_for_tenant(self, "hsn_code", value, "HSN Code")


class HSNItemTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = HSNItemType
        fields = ["id", "name"]


class HSNProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = HSNProductCategory
        fields = ["id", "name"]
