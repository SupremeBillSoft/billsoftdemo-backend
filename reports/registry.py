"""
Report registry: the ONE place that decides which masters can be reported on,
which columns are exposed, and how each master is tenant-scoped.

Adding a master to the report builder = adding one MasterReport(...) entry
below. Columns that must never appear in reports (tenant, secrets, internal
ids) are simply not listed; nothing is exposed automatically.
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from django.contrib.postgres.aggregates import StringAgg
from django.db.models import Case, CharField, Q, QuerySet, Value, When

from bank.models import Bank
from branch.models import Branch
from currency.models import Currency
from hsn.models import HSN
from items.models import Item
from parties.models import (
    Party,
    PartyBankDetail,
    PartyContact,
    PartyItem,
    PartyShippingAddress,
    PartySubTypeMaster,
)
from tax.models import Tax
from units.models import Unit

TEXT, NUMBER, BOOL, DATE, DATETIME = "text", "number", "bool", "date", "datetime"


@dataclass(frozen=True)
class Col:
    key: str                                  # name used by the API / frontend
    label: str                                # column heading in grid and Excel
    type: str = TEXT                          # text | number | bool | date | datetime
    path: Optional[str] = None                # ORM lookup; defaults to key (use "state__name" for FK display)
    default: bool = False                     # pre-selected when the master is chosen
    filterable: bool = True                   # False for computed/aggregated columns
    annotation: Optional[Callable[[], Any]] = None  # factory for computed columns
    group: str = ""                           # non-empty for parent columns in a child report (grouped/shaded in the picker)

    @property
    def lookup(self) -> str:
        return self.path or self.key


@dataclass(frozen=True)
class MasterReport:
    key: str
    label: str
    scope: Callable[[Any], QuerySet]          # tenant -> base queryset (THE tenant-isolation point)
    columns: Tuple[Col, ...]
    ordering: Tuple[str, ...] = ()            # child reports: parent first, so rows of one parent stay together
    parent_fk: Optional[str] = None           # child reports: FK column naming the parent row ("party_id")

    def get_column(self, key: str) -> Optional[Col]:
        for col in self.columns:
            if col.key == key:
                return col
        return None


# --------------------------------------------------------------------------- #
# Party Master
# --------------------------------------------------------------------------- #
PARTY = MasterReport(
    key="party",
    label="Party Master",
    # Same rule as PartyViewSet.get_queryset: never see another tenant's rows.
    scope=lambda tenant: Party.objects.filter(tenant=tenant),
    ordering=("party_code",),
    columns=(
        Col("party_code", "Party Code", default=True),
        Col("party_name", "Party Name", default=True),
        Col("business_type", "Business Type"),
        Col("category", "Category"),
        Col("is_customer", "Customer", BOOL),
        Col("is_vendor", "Vendor", BOOL),
        Col("is_export_customer", "Export Customer", BOOL),
        Col("is_rc_applicable", "RC Applicable", BOOL),
        Col(
            "sub_types", "Sub Types", filterable=False,
            annotation=lambda: StringAgg(
                "sub_types__sub_type_name", delimiter=", ", distinct=True, ordering="sub_types__sub_type_name"
            ),
        ),
        Col("address1", "Address Line 1"),
        Col("address2", "Address Line 2"),
        Col("city", "City", default=True),
        Col("pincode", "Pincode"),
        Col("state", "State", default=True),
        Col("country", "Country"),
        Col("mobile_no", "Mobile No", default=True),
        Col("email", "Email"),
        Col("office_phone", "Office Phone"),
        Col("website", "Website"),
        Col("gst_no", "GST No", default=True),
        Col("pan_no", "PAN No"),
        Col("vendor_code", "Vendor Code"),
        Col("party_contact_person", "Contact Person"),
        Col("party_area", "Area"),
        Col("range", "Range"),
        Col("division", "Division"),
        Col("party_distance", "Distance", NUMBER),
        Col("tcs_percentage", "TCS %", NUMBER),
        Col("tds_percentage", "TDS %", NUMBER),
        Col("commission_rate", "Commission Rate", NUMBER),
        Col("party_bank_name", "Bank Name"),
        Col("assessment_no", "Assessment No"),
        Col("evaluation_no", "Evaluation No"),
        Col("remarks", "Remarks"),
        Col("status", "Status", default=True),
        Col("approval_status", "Approved", BOOL),
        Col("approved_by", "Approved By"),
        Col("created_at", "Created On", DATETIME),
        Col("updated_at", "Updated On", DATETIME),
    ),
)

# --------------------------------------------------------------------------- #
# Party Sub Type Master  (two-tier: tenant NULL = system default)
# --------------------------------------------------------------------------- #
PARTY_SUB_TYPE = MasterReport(
    key="party_sub_type",
    label="Party Sub Type Master",
    # Same rule as PartySubTypeMasterViewSet: system defaults + this tenant's own.
    scope=lambda tenant: PartySubTypeMaster.objects.filter(Q(tenant__isnull=True) | Q(tenant=tenant)),
    ordering=("sub_type_name",),
    columns=(
        Col("sub_type_name", "Sub Type Name", default=True),
        Col("remarks", "Remarks", default=True),
        Col(
            "origin", "Origin", filterable=False, default=True,
            annotation=lambda: Case(
                When(tenant__isnull=True, then=Value("System")),
                default=Value("Custom"),
                output_field=CharField(),
            ),
        ),
    ),
)

# --------------------------------------------------------------------------- #
# Party child tables
#
# These tables have NO tenant column, so they are scoped THROUGH the parent:
# a child row is visible only when its Party (and, for Party Items, the Item it
# points at) belongs to the tenant. Each one is its own report: one row per
# child record, with the Party's columns selectable too (shown grouped/shaded in
# the column picker). Parent columns get the key prefix "parent_" and the
# group label "Party"; nothing is exposed automatically.
# --------------------------------------------------------------------------- #
def _child_scope(model, *via):
    """Tenant scope for a table without a tenant column: every relation in `via`
    must lead to a row owned by the tenant."""
    return lambda tenant: model.objects.filter(**{f"{rel}__tenant": tenant for rel in via})


def _with_parent(parent, rel, group, own, defaults=()):
    """Parent columns first (so they lead the grid), then the child's own."""
    taken = {c.label for c in own}
    parent_cols = tuple(
        Col(
            key=f"parent_{c.key}",
            label=f"{group} {c.label}" if c.label in taken else c.label,   # keep headings unambiguous
            type=c.type,
            path=f"{rel}__{c.lookup}",
            default=c.key in defaults,
            filterable=c.filterable,
            group=group,
        )
        for c in parent.columns
        if c.annotation is None   # aggregates over the parent's M2M make no sense per child row
    )
    return parent_cols + own


_PARTY_DEFAULTS = ("party_code", "party_name")

PARTY_CONTACT = MasterReport(
    key="party_contact",
    label="Party Contacts",
    scope=_child_scope(PartyContact, "party"),
    ordering=("party__party_code", "id"),
    parent_fk="party_id",
    columns=_with_parent(PARTY, "party", "Party", (
        Col("contact_person", "Contact Person", default=True),
        Col("department", "Department", default=True),
        Col("designation", "Designation", default=True),
        Col("mobile", "Mobile", default=True),
        Col("phone", "Phone"),
        Col("email", "Email", default=True),
    ), _PARTY_DEFAULTS),
)

# account_no is deliberately NOT listed (same decision as the Bank Master):
# add it here only once Keycloak permissions can restrict who may report on it.
PARTY_BANK = MasterReport(
    key="party_bank",
    label="Party Bank Details",
    scope=_child_scope(PartyBankDetail, "party"),
    ordering=("party__party_code", "id"),
    parent_fk="party_id",
    columns=_with_parent(PARTY, "party", "Party", (
        Col("bank_name", "Bank Name", default=True),
        Col("branch_name", "Branch Name", default=True),
        Col("account_type", "Account Type"),
        Col("ifsc_code", "IFSC Code", default=True),
        Col("switch_code", "Switch Code"),
        Col("is_default_account", "Default Account", BOOL, default=True),
        Col("status", "Status"),
        Col("remarks", "Remarks"),
    ), _PARTY_DEFAULTS),
)

PARTY_SHIPPING = MasterReport(
    key="party_shipping",
    label="Party Shipping Addresses",
    scope=_child_scope(PartyShippingAddress, "party"),
    ordering=("party__party_code", "id"),
    parent_fk="party_id",
    columns=_with_parent(PARTY, "party", "Party", (
        Col("shipping_party_name", "Ship-to Name", default=True),
        Col("shipping_address1", "Ship-to Address Line 1"),
        Col("shipping_address2", "Ship-to Address Line 2"),
        Col("shipping_city", "Ship-to City", default=True),
        Col("shipping_state", "Ship-to State"),
        Col("shipping_pincode", "Ship-to Pincode", default=True),
        Col("shipping_country", "Ship-to Country"),
        Col("shipping_mobile_no", "Ship-to Mobile No"),
        Col("shipping_email", "Ship-to Email"),
        Col("shipping_office_phone", "Ship-to Office Phone"),
        Col("shipping_gst_no", "Ship-to GST No"),
        Col("shipping_party_contact_person", "Ship-to Contact Person"),
    ), _PARTY_DEFAULTS),
)

PARTY_ITEM = MasterReport(
    key="party_item",
    label="Party Items",
    # both sides must be the tenant's own: the party AND the item
    scope=_child_scope(PartyItem, "party", "item"),
    ordering=("party__party_code", "item__item_code"),
    parent_fk="party_id",
    columns=_with_parent(PARTY, "party", "Party", (
        Col("item_code", "Item Code", path="item__item_code", default=True),
        Col("item_name", "Item Name", path="item__item_name", default=True),
        Col("item_type", "Item Type", path="item__item_type"),
        Col("item_category", "Item Category", path="item__item_category"),
        Col("hsn_code", "HSN Code", path="item__hsn_code"),
        Col("selling_price", "Selling Price", NUMBER, path="item__selling_price"),
        Col("discount_percentage", "Discount %", NUMBER, default=True),
        Col("item_code_in_print", "Item Code in Print", default=True),
    ), _PARTY_DEFAULTS),
)

# --------------------------------------------------------------------------- #
# Simple tenant-scoped masters
# --------------------------------------------------------------------------- #
def _scoped(model):
    """The standard tenant-isolation scope: this tenant's rows only."""
    return lambda tenant: model.objects.filter(tenant=tenant)


_AUDIT = (
    Col("created_at", "Created On", DATETIME),
    Col("updated_at", "Updated On", DATETIME),
)

ITEM = MasterReport(
    key="item",
    label="Item Master",
    scope=_scoped(Item),
    ordering=("item_code",),
    columns=(
        Col("item_code", "Item Code", default=True),
        Col("item_name", "Item Name", default=True),
        Col("short_name", "Short Name"),
        Col("item_type", "Item Type", default=True),
        Col("item_category", "Category", default=True),
        Col("brand", "Brand"),
        Col("item_description", "Description"),
        Col("item_status", "Status", default=True),
        Col("purchase_uom", "Purchase UOM"),
        Col("billing_uom", "Billing UOM"),
        Col("per_purchase_uom", "Per Purchase UOM", NUMBER),
        Col("hsn_code", "HSN Code"),
        Col("tax_type", "Tax Type"),
        Col("purchase_price", "Purchase Price", NUMBER),
        Col("selling_price", "Selling Price", NUMBER, default=True),
        Col("mrp", "MRP", NUMBER),
        Col("wholesale_discount", "Wholesale Discount", NUMBER),
        Col("retail_discount", "Retail Discount", NUMBER),
        Col("member_discount", "Member Discount", NUMBER),
        Col("recipe_cost", "Recipe Cost", NUMBER),
        Col("dine_in_price", "Dine-in Price", NUMBER),
        Col("takeaway_price", "Takeaway Price", NUMBER),
        Col("delivery_price", "Delivery Price", NUMBER),
        Col("per_unit_weight", "Per Unit Weight", NUMBER),
        Col("packing_qty", "Packing Qty", NUMBER),
        Col("reorder_level", "Reorder Level", NUMBER),
        Col("drawing_no", "Drawing No"),
        Col("procurement_days", "Procurement Days", NUMBER),
    ) + _AUDIT,
)

TAX = MasterReport(
    key="tax",
    label="Tax Master",
    scope=_scoped(Tax),
    ordering=("tax_type", "tax_name"),
    columns=(
        Col("tax_type", "Tax Type", default=True),
        Col("tax_name", "Tax Name", default=True),
        Col("cgst_percentage", "CGST %", NUMBER, default=True),
        Col("sgst_percentage", "SGST %", NUMBER, default=True),
        Col("igst_percentage", "IGST %", NUMBER, default=True),
        Col("cess_percentage", "Cess %", NUMBER),
        Col("vat_percentage", "VAT %", NUMBER),
        Col("sales_tax_percentage", "Sales Tax %", NUMBER),
        Col("status", "Status", default=True),
    ) + _AUDIT,
)

HSN_MASTER = MasterReport(
    key="hsn",
    label="HSN Master",
    scope=_scoped(HSN),
    ordering=("hsn_code",),
    columns=(
        Col("hsn_code", "HSN Code", default=True),
        Col("hsn_name", "HSN Name", default=True),
        Col("item_type", "Item Type", default=True),
        Col("product_category", "Product Category", default=True),
        Col("tax_type", "Tax Type", default=True),
        Col("status", "Status", default=True),
    ) + _AUDIT,
)

UNIT = MasterReport(
    key="unit",
    label="Unit Master",
    scope=_scoped(Unit),
    ordering=("unit_code",),
    columns=(
        Col("unit_code", "Unit Code", default=True),
        Col("unit_name", "Unit Name", default=True),
        Col("unit_description", "Description", default=True),
        Col("einvoice_uom", "E-Invoice UOM", default=True),
        Col("unit_status", "Status", default=True),
        Col("remarks", "Remarks"),
    ) + _AUDIT,
)

# account_no is deliberately NOT listed: add it here only once Keycloak permissions can restrict who may report on it.
BANK = MasterReport(
    key="bank",
    label="Bank Master",
    scope=_scoped(Bank),
    ordering=("bank_code",),
    columns=(
        Col("bank_code", "Bank Code", default=True),
        Col("bank_name", "Bank Name", default=True),
        Col("branch_name", "Branch Name", default=True),
        Col("address", "Address"),
        Col("account_type", "Account Type"),
        Col("ifsc_code", "IFSC Code", default=True),
        Col("micr_code", "MICR Code"),
        Col("opening_balance", "Opening Balance", NUMBER),
        Col("remarks", "Remarks"),
        Col("status", "Status", default=True),
    ) + _AUDIT,
)

BRANCH = MasterReport(
    key="branch",
    label="Branch Master",
    scope=_scoped(Branch),
    ordering=("branch_code",),
    columns=(
        Col("branch_code", "Branch Code", default=True),
        Col("branch_name", "Branch Name", default=True),
        Col("branch_type", "Branch Type", default=True),
        Col("registration_no", "Registration No"),
        Col("gstin", "GSTIN"),
        Col("pan", "PAN"),
        Col("address_line_1", "Address Line 1"),
        Col("address_line_2", "Address Line 2"),
        Col("area_locality", "Area / Locality"),
        Col("city", "City", default=True),
        Col("state", "State", default=True),
        Col("country", "Country"),
        Col("pincode", "Pincode"),
        Col("mobile_no", "Mobile No"),
        Col("email", "Email"),
        Col("remarks", "Remarks"),
        Col("status", "Status", default=True),
    ) + _AUDIT,
)

CURRENCY = MasterReport(
    key="currency",
    label="Currency Master",
    scope=_scoped(Currency),
    ordering=("currency_code",),
    columns=(
        Col("currency_code", "Currency Code", default=True),
        Col("currency_name", "Currency Name", default=True),
        Col("symbol", "Symbol", default=True),
        Col("major_unit_name", "Major Unit"),
        Col("major_unit_name_plural", "Major Unit (Plural)"),
        Col("minor_unit_name", "Minor Unit"),
        Col("minor_unit_name_plural", "Minor Unit (Plural)"),
        Col("decimal_places", "Decimal Places", NUMBER, default=True),
        Col("symbol_position", "Symbol Position"),
        Col("status", "Status", default=True),
    ) + _AUDIT,
)

REGISTRY = {
    m.key: m
    for m in (PARTY, PARTY_CONTACT, PARTY_BANK, PARTY_SHIPPING, PARTY_ITEM, PARTY_SUB_TYPE, ITEM, TAX, HSN_MASTER, UNIT, BANK, BRANCH, CURRENCY)
}


def get_master(key: str) -> Optional[MasterReport]:
    return REGISTRY.get(key)


def all_masters():
    return list(REGISTRY.values())
