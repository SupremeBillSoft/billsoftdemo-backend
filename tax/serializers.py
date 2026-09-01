# tax/serializers.py

from rest_framework import serializers

from .models import Tax


class TaxSerializer(serializers.ModelSerializer):
    tax_code = serializers.IntegerField(source="id", read_only=True)  # one-line: exposes the PK under
                                                                    #the name the frontend expects

    class Meta:
        model = Tax
        fields = ["id", "tax_code", "tax_type", "tax_name", "cgst_percentage", "sgst_percentage", "igst_percentage",
                  "cess_percentage", "vat_percentage", "sales_tax_percentage", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "tax_code", "created_at", "updated_at"]
