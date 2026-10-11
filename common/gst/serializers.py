# common/gst/serializers.py

from rest_framework import serializers


class GstLineCalculationRequestSerializer(serializers.Serializer):
    taxable_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    gst_rate_percent = serializers.DecimalField(max_digits=5, decimal_places=2)
    cess_rate_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=0
    )
    seller_state = serializers.CharField()
    buyer_state = serializers.CharField()
    rcm_applicable = serializers.BooleanField(required=False, default=False)
