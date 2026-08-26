# items/serializers.py

from rest_framework import serializers

from .models import Item, ItemCustomField

CUSTOM_FIELD_LIMITS = {"Text": 6, "Date": 2, "Float": 2}
CUSTOM_FIELD_TOTAL_LIMIT = 10


class ItemCustomFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemCustomField
        fields = ["id", "field_type", "field_name", "text_value", "date_value", "float_value"]

    def validate(self, data):
        # one-line: mirrors the frontend's discriminated-union rule — a
        # Date-type custom field should carry date_value, not text_value,
        # etc. Prevents a Float field silently accepting garbage in
        # text_value if a client sends the wrong shape.
        field_type = data.get("field_type")
        value_map = {"Text": "text_value", "Date": "date_value", "Float": "float_value"}
        expected_field = value_map.get(field_type)
        provided = [k for k in value_map.values() if data.get(k) is not None]
        if provided and provided != [expected_field]:
            raise serializers.ValidationError(f"A {field_type} custom field must only set {expected_field}")
        return data


class ItemSerializer(serializers.ModelSerializer):
    custom_fields = ItemCustomFieldSerializer(many=True, required=False)

    class Meta:
        model = Item
        fields = [
            "id", "item_code", "item_name", "item_type", "item_description", "item_category", "brand", "item_status",
            "purchase_uom", "billing_uom", "per_purchase_uom",
            "hsn_code", "tax_type", "purchase_price", "selling_price", "mrp",
            "wholesale_discount", "retail_discount", "member_discount",
            "recipe_cost", "dine_in_price", "takeaway_price", "delivery_price",
            "per_unit_weight", "packing_qty", "reorder_level", "drawing_no", "procurement_days", "short_name",
            "custom_fields", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_item_code(self, value):
        if not value.isalnum():
            raise serializers.ValidationError("Item Code must be alphanumeric")
        return value

    def validate_custom_fields(self, value):
        # one-line: THE server-side enforcement of the 6/2/2/10 caps — the
        # frontend disables the Type dropdown option live to prevent this in
        # the UI, but that's a UX nicety, not security; this is the real
        # backstop, same as every other client-side validation in this app.
        counts = {}
        for field in value:
            field_type = field["field_type"]
            counts[field_type] = counts.get(field_type, 0) + 1

        for field_type, limit in CUSTOM_FIELD_LIMITS.items():
            if counts.get(field_type, 0) > limit:
                raise serializers.ValidationError(f"Maximum {limit} {field_type} custom fields allowed")
        if len(value) > CUSTOM_FIELD_TOTAL_LIMIT:
            raise serializers.ValidationError(f"Maximum {CUSTOM_FIELD_TOTAL_LIMIT} custom fields allowed")
        return value

    def create(self, validated_data):
        custom_fields_data = validated_data.pop("custom_fields", [])
        item = Item.objects.create(**validated_data)
        for field in custom_fields_data:
            ItemCustomField.objects.create(item=item, **field)
        return item

    def update(self, instance, validated_data):
        custom_fields_data = validated_data.pop("custom_fields", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if custom_fields_data is not None:
            instance.custom_fields.all().delete()
            for field in custom_fields_data:
                ItemCustomField.objects.create(item=instance, **field)

        return instance
