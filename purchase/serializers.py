# purchase/serializers.py

import re

from rest_framework import serializers

from .models import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderTerm,
    PurchaseRequisition,
    PurchaseRequisitionItem,
)


class PurchaseRequisitionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseRequisitionItem
        fields = ["id", "item_code", "description", "uom", "hsn_sac", "qty", "delivery_date"]


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    items = PurchaseRequisitionItemSerializer(many=True, required=False)
    supplier_code = serializers.CharField(source="supplier.party_code", read_only=True)
    supplier_name = serializers.CharField(source="supplier.party_name", read_only=True)

    class Meta:
        model = PurchaseRequisition
        fields = ["id", "pr_no", "pr_type", "supplier", "supplier_code", "supplier_name", "raised_by", "pr_date",
                  "format_no", "approval_status", "approved_by", "reason", "items", "created_at", "updated_at"]
        read_only_fields = ["id", "pr_no", "approval_status", "approved_by", "reason", "created_at", "updated_at"]

    def validate(self, data):
        # one-line: THE PR Type rule — Item Based requires at least one row,
        # General doesn't. Re-checked server-side, same as every other
        # client-enforced rule in this app.
        pr_type = data.get("pr_type", "Item Based")
        items = data.get("items", [])
        if pr_type == "Item Based" and not items:
            raise serializers.ValidationError({"items": "At least one item is required for an Item Based PR."})
        return data

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        pr = PurchaseRequisition.objects.create(**validated_data)
        for item in items_data:
            PurchaseRequisitionItem.objects.create(pr=pr, **item)
        return pr

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                PurchaseRequisitionItem.objects.create(pr=instance, **item)
        return instance


class PurchaseRequisitionApprovalSerializer(serializers.Serializer):
    approval_status = serializers.ChoiceField(choices=["Approved", "Hold", "Rejected"])
    approved_by = serializers.CharField(max_length=50)
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


def _extract_tax_percentage(tax_type_label: str) -> float:
    # one-line: parses "18% GST" (Item Master's Tax Type format) back into
    # 18.0 — the leading number is always the rate, by construction of how
    # that label was built (taxName + "%" + taxType) in Item/HSN Master.
    match = re.match(r"^\s*([\d.]+)\s*%", tax_type_label or "")
    return float(match.group(1)) if match else 0.0


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderItem
        fields = ["id", "item_code", "description", "qty", "uom", "rate", "discount_percentage", "value",
                  "pack_forwarding", "freight_amount", "tax_type", "tax_amount", "hsn_sac",
                  "expected_delivery_date", "notes"]
        read_only_fields = ["id", "value", "tax_amount"]


class PurchaseOrderTermSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderTerm
        fields = ["id", "text"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, required=False)
    terms = PurchaseOrderTermSerializer(many=True, required=False)
    supplier_code = serializers.CharField(source="supplier.party_code", read_only=True)
    supplier_name = serializers.CharField(source="supplier.party_name", read_only=True)
    pr_no = serializers.CharField(source="pr.pr_no", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "po_no", "supplier", "supplier_code", "supplier_name", "pr", "pr_no", "bill_no", "format_no",
                  "quotation_no", "quotation_date", "po_date", "validity", "payment_terms", "document",
                  "despatch_through", "currency", "delivery", "kind_attn", "other_ref", "delivery_to",
                  "goods_value", "cgst_amount", "sgst_amount", "igst_amount", "tax_value", "net_value",
                  "approval_status", "approved_by", "reason", "items", "terms", "created_at", "updated_at"]
        read_only_fields = ["id", "po_no", "goods_value", "cgst_amount", "sgst_amount", "igst_amount",
                             "tax_value", "net_value", "approval_status", "approved_by", "reason",
                             "created_at", "updated_at"]

    def _compute_totals(self, po, items_data):
        goods_value = 0.0
        tax_value = 0.0
        pack_freight_total = 0.0

        for item in items_data:
            gross = item["qty"] * item["rate"]
            value = gross * (1 - item.get("discount_percentage", 0) / 100)
            item["value"] = round(value, 2)

            tax_pct = _extract_tax_percentage(item.get("tax_type", ""))
            item["tax_amount"] = round(value * tax_pct / 100, 2)

            goods_value += item["value"]
            tax_value += item["tax_amount"]
            pack_freight_total += item.get("pack_forwarding", 0) + item.get("freight_amount", 0)

        # one-line: split assumption flagged above — even CGST/SGST split,
        # zero IGST. Revisit once an interstate/intrastate flag exists.
        po.goods_value = round(goods_value, 2)
        po.cgst_amount = round(tax_value / 2, 2)
        po.sgst_amount = round(tax_value / 2, 2)
        po.igst_amount = 0.0
        po.tax_value = round(tax_value, 2)
        po.net_value = round(goods_value + tax_value + pack_freight_total, 2)

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        terms_data = validated_data.pop("terms", [])
        po = PurchaseOrder(**validated_data)
        self._compute_totals(po, items_data)
        po.save()
        for item in items_data:
            PurchaseOrderItem.objects.create(po=po, **item)
        for term in terms_data:
            PurchaseOrderTerm.objects.create(po=po, **term)
        return po

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        terms_data = validated_data.pop("terms", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if items_data is not None:
            self._compute_totals(instance, items_data)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                PurchaseOrderItem.objects.create(po=instance, **item)
        if terms_data is not None:
            instance.terms.all().delete()
            for term in terms_data:
                PurchaseOrderTerm.objects.create(po=instance, **term)
        return instance


class PurchaseOrderApprovalSerializer(serializers.Serializer):
    approval_status = serializers.ChoiceField(choices=["Approved", "Hold", "Rejected"])
    approved_by = serializers.CharField(max_length=50)
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)
