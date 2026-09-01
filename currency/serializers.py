# currency/serializers.py
from rest_framework import serializers

from .models import Currency


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ["id", "currency_code", "currency_name", "symbol", "major_unit_name",
                  "minor_unit_name", "decimal_places", "symbol_position", "status",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
