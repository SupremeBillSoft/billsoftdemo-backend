# bank/serializers.py

from rest_framework import serializers

from .models import Bank


class BankSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bank
        fields = ["id", "bank_code", "bank_name", "branch_name", "address", "account_no", "account_type",
                  "ifsc_code", "micr_code", "remarks", "opening_balance", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_bank_code(self, value):
        if not value.isalnum():
            raise serializers.ValidationError("Bank Code must be alphanumeric")
        return value
