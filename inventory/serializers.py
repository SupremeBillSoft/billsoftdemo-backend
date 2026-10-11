# inventory/serializers.py

from decimal import Decimal

from rest_framework import serializers

from items.models import Item

from .models import Godown, StockMovement, StockTransfer, StockTransferItem


class GodownSerializer(serializers.ModelSerializer):
    class Meta:
        model = Godown
        fields = ["id", "name", "location", "state", "description", "is_main", "status"]

    def validate_name(self, value: str) -> str:
        tenant = self.context["tenant"]
        clash = Godown.objects.filter(tenant=tenant, name__iexact=value.strip())
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A godown with this name already exists.")
        return value.strip()


class StockAdjustmentRequestSerializer(serializers.Serializer):
    ADD = "Add"
    REDUCE = "Reduce"

    item = serializers.PrimaryKeyRelatedField(queryset=Item.objects.all())
    godown = serializers.PrimaryKeyRelatedField(queryset=Godown.objects.all())
    adjustment_type = serializers.ChoiceField(choices=[ADD, REDUCE])
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    date = serializers.DateField()
    remarks = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        tenant = self.context["tenant"]
        if attrs["item"].tenant_id != tenant.pk or attrs["godown"].tenant_id != tenant.pk:
            raise serializers.ValidationError("Item or godown not found.")
        return attrs


class StockAdjustmentHistorySerializer(serializers.ModelSerializer):
    """BRD 6.3.1 item 13: Old Stock, Updated Stock, Adjusted Qty, Updated
    By, Updated On."""

    godown_name = serializers.CharField(source="godown.name", read_only=True)
    old_stock = serializers.DecimalField(source="balance_before", max_digits=14, decimal_places=3, read_only=True)
    updated_stock = serializers.DecimalField(source="balance_after", max_digits=14, decimal_places=3,
                                             read_only=True)
    adjusted_qty = serializers.DecimalField(source="quantity", max_digits=14, decimal_places=3, read_only=True)
    updated_by = serializers.CharField(source="created_by", read_only=True)
    updated_on = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = StockMovement
        fields = ["id", "movement_date", "movement_type", "godown", "godown_name", "old_stock", "updated_stock",
                  "adjusted_qty", "remarks", "updated_by", "updated_on"]


class StockTransferItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.item_code", read_only=True)
    item_name = serializers.CharField(source="item.item_name", read_only=True)

    class Meta:
        model = StockTransferItem
        fields = ["id", "item", "item_code", "item_name", "quantity"]

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than 0.")
        return value


class StockTransferSerializer(serializers.ModelSerializer):
    items = StockTransferItemSerializer(many=True)
    transfer_number = serializers.CharField(read_only=True)
    from_godown_name = serializers.CharField(source="from_godown.name", read_only=True)
    to_godown_name = serializers.CharField(source="to_godown.name", read_only=True)

    class Meta:
        model = StockTransfer
        fields = ["id", "transfer_number", "from_godown", "from_godown_name", "to_godown", "to_godown_name",
                  "transfer_date", "remarks", "created_by", "created_at", "items"]
        read_only_fields = ["created_by", "created_at"]

    def validate(self, attrs):
        tenant = self.context["tenant"]
        if attrs["from_godown"].id == attrs["to_godown"].id:
            raise serializers.ValidationError({"to_godown": "Source and destination godown must be different."})
        if attrs["from_godown"].tenant_id != tenant.pk or attrs["to_godown"].tenant_id != tenant.pk:
            raise serializers.ValidationError("Godown not found.")
        if not attrs.get("items"):
            raise serializers.ValidationError({"items": "Select at least one item to transfer."})
        for line in attrs["items"]:
            if line["item"].tenant_id != tenant.pk or line["item"].item_type != "Product":
                raise serializers.ValidationError({"items": f"'{line['item']}' is not a stocked item."})
        return attrs
