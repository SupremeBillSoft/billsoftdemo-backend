# parties/models.py

from django.db import models

from tenants.models import Tenant


class PartySubTypeMaster(models.Model):
    # one-line: tenant_id is NULLABLE here on purpose — this is the two-tier
    # design we agreed on. NULL = system-wide default (Sub Contractor,
    # Supplier, etc.), a real value = one tenant's own custom addition.
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name="custom_sub_types")
    sub_type_name = models.CharField(max_length=25)
    remarks = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "party_sub_type_master"
        # one-line: prevents a tenant from creating the same sub-type name
        # twice, and prevents duplicate SYSTEM defaults with tenant=NULL —
        # Postgres treats each NULL as distinct by default, so this
        # constraint only actually protects tenant-scoped rows; the system
        # defaults are protected by simply not letting anyone add tenant=NULL
        # rows through the API (enforced in the serializer, not the DB).
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sub_type_name"], name="uniq_tenant_subtype_name")
        ]

    def __str__(self):
        return self.sub_type_name


class Party(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="parties")

    party_code = models.CharField(max_length=10)
    party_name = models.CharField(max_length=150)

    address1 = models.CharField(max_length=100, blank=True)
    address2 = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=50, blank=True)
    pincode = models.CharField(max_length=20, blank=True)
    state = models.CharField(max_length=50, blank=True)
    country = models.CharField(max_length=50, blank=True)

    mobile_no = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=50, blank=True)
    office_phone = models.CharField(max_length=50, blank=True)
    website = models.CharField(max_length=50, blank=True)

    gst_no = models.CharField(max_length=15, blank=True)
    pan_no = models.CharField(max_length=15, blank=True)
    vendor_code = models.CharField(max_length=50, blank=True)
    party_contact_person = models.CharField(max_length=50, blank=True)
    party_area = models.CharField(max_length=50, blank=True)
    range = models.CharField(max_length=50, blank=True)
    division = models.CharField(max_length=50, blank=True)
    party_distance = models.FloatField(null=True, blank=True)
    remarks = models.CharField(max_length=100, blank=True)

    business_type = models.CharField(max_length=10, blank=True)
    is_vendor = models.BooleanField(default=False)
    is_customer = models.BooleanField(default=False)
    is_export_customer = models.BooleanField(default=False)
    is_rc_applicable = models.BooleanField(default=False)
    tcs_percentage = models.FloatField(null=True, blank=True)
    tds_percentage = models.FloatField(null=True, blank=True)
    party_bank_name = models.CharField(max_length=50, blank=True)
    assessment_no = models.CharField(max_length=50, blank=True)
    evaluation_no = models.CharField(max_length=50, blank=True)
    commission_rate = models.FloatField(null=True, blank=True)
    category = models.CharField(max_length=50, blank=True)

    status = models.CharField(max_length=50, default="Active")
    approval_status = models.BooleanField(default=False)
    approved_by = models.CharField(max_length=50, blank=True)

    sub_types = models.ManyToManyField(PartySubTypeMaster, through="PartySubType", related_name="parties")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "party_master"
        constraints = [
            # one-line: THE actual tenant-isolation enforcement, in this
            # design — not a composite PK anymore, but this constraint does
            # the same real job: no two rows for the same tenant can share
            # a party_code. Cross-tenant duplicates are fine and expected.
            models.UniqueConstraint(fields=["tenant", "party_code"], name="uniq_tenant_party_code")
        ]

    def __str__(self):
        return f"{self.party_code} — {self.party_name}"


class PartySubType(models.Model):
    # one-line: the junction table itself — Django needs this as an explicit
    # model (not just the M2M field above) because we may later want extra
    # columns on the relationship itself (e.g. an assigned_date).
    id = models.BigAutoField(primary_key=True)
    party = models.ForeignKey(Party, on_delete=models.CASCADE)
    sub_type = models.ForeignKey(PartySubTypeMaster, on_delete=models.CASCADE)

    class Meta:
        db_table = "party_sub_type"
        constraints = [
            models.UniqueConstraint(fields=["party", "sub_type"], name="uniq_party_subtype")
        ]


class PartyContact(models.Model):
    id = models.BigAutoField(primary_key=True)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="contacts")
    contact_person = models.CharField(max_length=50)
    department = models.CharField(max_length=50, blank=True)
    designation = models.CharField(max_length=50, blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(max_length=50, blank=True)

    class Meta:
        db_table = "party_contacts"


class PartyItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="items")
    # one-line: string reference "items.Item" — the Item model lives in a
    # different app we haven't created the models for yet this pass; Django
    # resolves this lazily, no import needed. See Item Master follow-up.
    item = models.ForeignKey("items.Item", on_delete=models.CASCADE, related_name="party_prices")
    discount_percentage = models.FloatField(null=True, blank=True)
    item_code_in_print = models.CharField(max_length=50)

    class Meta:
        db_table = "party_items"
        constraints = [
            models.UniqueConstraint(fields=["party", "item"], name="uniq_party_item")
        ]


class PartyBankDetail(models.Model):
    id = models.BigAutoField(primary_key=True)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="bank_accounts")
    bank_name = models.CharField(max_length=50)
    branch_name = models.CharField(max_length=50, blank=True)
    account_no = models.CharField(max_length=50)
    account_type = models.CharField(max_length=20, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    switch_code = models.CharField(max_length=20, blank=True)
    is_default_account = models.BooleanField(default=False)
    status = models.CharField(max_length=50, default="Active")
    remarks = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "party_bank_details"


class PartyShippingAddress(models.Model):
    id = models.BigAutoField(primary_key=True)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="shipping_addresses")
    shipping_party_name = models.CharField(max_length=150, blank=True)
    shipping_address1 = models.CharField(max_length=100, blank=True)
    shipping_address2 = models.CharField(max_length=100, blank=True)
    shipping_city = models.CharField(max_length=50, blank=True)
    shipping_pincode = models.CharField(max_length=20, blank=True)
    shipping_state = models.CharField(max_length=50, blank=True)
    shipping_country = models.CharField(max_length=50, blank=True)
    shipping_mobile_no = models.CharField(max_length=20, blank=True)
    shipping_email = models.EmailField(max_length=50, blank=True)
    shipping_office_phone = models.CharField(max_length=50, blank=True)
    shipping_gst_no = models.CharField(max_length=15, blank=True)
    shipping_party_contact_person = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = "party_shipping_address"
