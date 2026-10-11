# tenants/models.py

import uuid

from django.db import models


class Tenant(models.Model):
    # one-line: UUID, not bigint — this ID gets referenced externally
    # (subdomains, API tokens), where non-guessable IDs are a real security
    # property. See the earlier reasoning: bigint everywhere EXCEPT tenant_id.
    tenant_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subdomain = models.CharField(max_length=63, unique=True)
    tenant_name = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant"

    def __str__(self):
        return self.tenant_name

class DocNumberSequence(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="doc_sequences")
    doc_type = models.CharField(max_length=20)  # "PARTY" now; "PURCHASE_REQUEST"/"PURCHASE_ORDER" later
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "doc_number_sequence"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "doc_type"], name="uniq_tenant_doctype_sequence")
        ]


# BRD User Management roles.
USER_ROLES = ["Super Admin", "Admin", "Manager", "User"]


class CompanySettings(models.Model):
    """One row per tenant (BRD 6.7.1 Company Settings + the registration
    page). Holds the company's own State/GSTIN - the "seller" side of the
    intra/inter-state GST decision - and the purchase auto-approval
    switches (BRD 6.5.1 / 6.5.2: "PR/PO auto approval enabled in settings")."""

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="company_settings")

    business_name = models.CharField(max_length=150, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(max_length=100, blank=True, default="")
    address_1 = models.CharField(max_length=150, blank=True, default="")
    address_2 = models.CharField(max_length=150, blank=True, default="")
    city = models.CharField(max_length=50, blank=True, default="")
    pincode = models.CharField(max_length=10, blank=True, default="")
    state = models.CharField(max_length=50, blank=True, default="")
    country = models.CharField(max_length=50, blank=True, default="India")

    gst_registered = models.BooleanField(default=False)
    gstin = models.CharField(max_length=15, blank=True, default="")
    pan_no = models.CharField(max_length=10, blank=True, default="")

    # BRD 6.7.1 item 13 / Financial Year Management. Drives the year block
    # in document numbers. 4 = April (Indian GST financial year).
    financial_year_start_month = models.PositiveSmallIntegerField(default=4)

    # Legacy Utility_Control ISPoWithoutPR (default N): when off, every PO
    # line must come from an Approved PR.
    allow_po_without_pr = models.BooleanField(default=False)

    # Legacy Utility_Control IsPrAutoApprove / IsPoAutoApprove, made
    # role-based (confirmed: e.g. a PR created by a Manager is approved
    # automatically). Lists of USER_ROLES values. No login yet, so the
    # creator's role is unknown and nothing auto-approves until login lands
    # - see tenants.services.current_user_role.
    auto_approve_pr_roles = models.JSONField(default=list, blank=True)
    auto_approve_po_roles = models.JSONField(default=list, blank=True)

    # --- Sales ---------------------------------------------------------
    # BRD 6.4.5 3.1: sales may be billed ahead of stock, with a warning.
    # Purchases, returns and transfers never go below zero regardless.
    allow_negative_stock_on_sales = models.BooleanField(default=True)

    # GST e-invoicing (BRD 6.4.6). Mandatory once aggregate turnover
    # exceeds Rs 5 crore; turnover of Rs 10 crore+ must report each
    # document to the IRP within 30 days of its date.
    einvoice_enabled = models.BooleanField(default=False)
    aato_10cr_or_more = models.BooleanField(default=False)

    # E-way bill (BRD 6.4.7): required above this consignment value. Some
    # states set a higher limit for intra-state moves: {"Maharashtra": 100000}.
    eway_bill_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=50000)
    eway_bill_intra_state_thresholds = models.JSONField(default=dict, blank=True)

    # Letter of Undertaking for zero-rated exports without IGST payment.
    lut_number = models.CharField(max_length=50, blank=True, default="")
    lut_valid_till = models.DateField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_settings"

    def __str__(self):
        return f"Company settings for {self.tenant}"
