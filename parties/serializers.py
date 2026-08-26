# parties/serializers.py

from rest_framework import serializers

from items.models import Item

from .models import (
    Party,
    PartyBankDetail,
    PartyContact,
    PartyItem,
    PartyShippingAddress,
    PartySubTypeMaster,
)


class PartyContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartyContact
        fields = ["id", "contact_person", "department", "designation", "mobile", "phone", "email"]
        # one-line: "id" is read-only by default in DRF for existing rows —
        # included here so an UPDATE can tell "this row already exists,
        # patch it" apart from "this is a new row to create."


class PartyItemSerializer(serializers.ModelSerializer):
    # one-line: the frontend's repeatable Item Details table works with
    # item_code (a string) end to end — the client never needs to know or
    # send our internal bigint `id`. SlugRelatedField does that translation:
    # accepts/returns item_code, resolves to/from the real Item FK internally.
    item_code = serializers.SlugRelatedField(source="item", slug_field="item_code", queryset=Item.objects.all())

    class Meta:
        model = PartyItem
        fields = ["id", "item_code", "discount_percentage", "item_code_in_print"]


class PartyBankDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartyBankDetail
        fields = ["id", "bank_name", "branch_name", "account_no", "account_type", "ifsc_code", "switch_code",
                   "is_default_account", "status", "remarks"]


class PartyShippingAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartyShippingAddress
        fields = [
            "id", "shipping_party_name", "shipping_address1", "shipping_address2", "shipping_city",
            "shipping_pincode", "shipping_state", "shipping_country", "shipping_mobile_no",
            "shipping_email", "shipping_office_phone", "shipping_gst_no", "shipping_party_contact_person",
        ]


class PartySubTypeMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartySubTypeMaster
        fields = ["id", "sub_type_name", "remarks"]


class PartySerializer(serializers.ModelSerializer):
    # one-line: nested writable serializers — DRF's ModelSerializer does NOT
    # handle nested create/update automatically; the create()/update()
    # overrides below are what actually make these four lists save correctly.
    contacts = PartyContactSerializer(many=True, required=False)
    items = PartyItemSerializer(many=True, required=False)
    bank_accounts = PartyBankDetailSerializer(many=True, required=False)
    shipping_addresses = PartyShippingAddressSerializer(many=True, required=False)
    sub_type_ids = serializers.PrimaryKeyRelatedField(
        source="sub_types", many=True, required=False, queryset=PartySubTypeMaster.objects.all()
    )

    class Meta:
        model = Party
        fields = [
            "id", "party_code", "party_name",
            "address1", "address2", "city", "pincode", "state", "country",
            "mobile_no", "email", "office_phone", "website",
            "gst_no", "pan_no", "vendor_code", "party_contact_person", "party_area",
            "range", "division", "party_distance", "remarks",
            "business_type", "is_vendor", "is_customer", "is_export_customer", "is_rc_applicable",
            "tcs_percentage", "tds_percentage", "party_bank_name", "assessment_no", "evaluation_no",
            "commission_rate", "category", "status", "approval_status", "approved_by",
            "contacts", "items", "bank_accounts", "shipping_addresses", "sub_type_ids",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, data):
            # TODO — HARDCODED, MUST FIX BEFORE PRODUCTION:
        # This should read tenant.business_information.einvoice_status, not a
        # literal True. Currently hardcoded because Business_Information isn't
        # modeled yet. Whoever builds that model MUST come back to this exact
        # line and wire it correctly — a party could otherwise get created
        # without GST/Address validation even when the tenant actually has
        # e-invoicing enabled, which is a real compliance gap, not just a bug.
        einvoice_enabled = True  # TODO: replace with tenant.business_information.einvoice_status once Business_Informationismodeled  # noqa: E501
        gst_required = einvoice_enabled and (data.get("is_customer") or data.get("is_export_customer"))
        if gst_required:
            missing = [f for f in ["gst_no", "address1", "city", "pincode"] if not data.get(f)]
            if missing:
                raise serializers.ValidationError(
                    {f: "Required when e-invoice is enabled for a customer" for f in missing}
                )
        return data

    def validate_party_code(self, value):
        if not value.isalnum():
            raise serializers.ValidationError("Party Code must be alphanumeric")
        return value

    def create(self, validated_data):
        contacts_data = validated_data.pop("contacts", [])
        items_data = validated_data.pop("items", [])
        bank_data = validated_data.pop("bank_accounts", [])
        shipping_data = validated_data.pop("shipping_addresses", [])
        sub_types_data = validated_data.pop("sub_types", [])

        # one-line: tenant is injected by the VIEWSET's perform_create, not
        # accepted from validated_data — the serializer never even declares
        # a "tenant" field, so there's no way for a client-supplied tenant_id
        # to reach this point even by accident.
        party = Party.objects.create(**validated_data)

        for contact in contacts_data:
            PartyContact.objects.create(party=party, **contact)
        for item in items_data:
            PartyItem.objects.create(party=party, **item)
        for bank in bank_data:
            PartyBankDetail.objects.create(party=party, **bank)
        for shipping in shipping_data:
            PartyShippingAddress.objects.create(party=party, **shipping)
        if sub_types_data:
            party.sub_types.set(sub_types_data)

        return party

    def update(self, instance, validated_data):
        contacts_data = validated_data.pop("contacts", None)
        items_data = validated_data.pop("items", None)
        bank_data = validated_data.pop("bank_accounts", None)
        shipping_data = validated_data.pop("shipping_addresses", None)
        sub_types_data = validated_data.pop("sub_types", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # one-line: replace-all strategy for nested lists on update — simplest
        # correct behavior for a repeatable-tab UI where the frontend always
        # sends the FULL current list on save, not a diff. If we ever need
        # partial/incremental updates to just one row, this needs revisiting.
        if contacts_data is not None:
            instance.contacts.all().delete()
            for contact in contacts_data:
                PartyContact.objects.create(party=instance, **contact)
        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                PartyItem.objects.create(party=instance, **item)
        if bank_data is not None:
            instance.bank_accounts.all().delete()
            for bank in bank_data:
                PartyBankDetail.objects.create(party=instance, **bank)
        if shipping_data is not None:
            instance.shipping_addresses.all().delete()
            for shipping in shipping_data:
                PartyShippingAddress.objects.create(party=instance, **shipping)
        if sub_types_data is not None:
            instance.sub_types.set(sub_types_data)

        return instance

