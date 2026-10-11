# purchase/gst_resolution.py
#
# The ONE place purchase documents resolve "which tax and whose state"
# before calling common.gst.engine.compute_line_tax - kept separate from the
# pure engine itself (architecture doc section 4.2: the engine never looks
# these up on its own).
#
# Tax selection (BRD 6.5.2.1.2 "Tax Type" per line, 6.4.2 "auto fetch Tax %
# from Item master ... allow user to change"):
#   - a line may carry an explicit Tax master row chosen by the user;
#   - otherwise the default is the Item's own tax_type, falling back to the
#     HSN row's tax_type (via Item.hsn_code). The Item / HSN screens save the
#     tax as its display label ("18% GST" = "{tax_name}% {tax_type}"); older
#     rows hold a Tax.id or free text like "GST 18%" - all are matched by
#     match_tax_code below.
#
# Rate from a Tax master row: intra-state uses CGST% + SGST%, inter-state
# uses IGST% - each column is used for the case it describes, rather than
# trusting one column for both (a row with CGST 9 / SGST 9 / IGST 9 would
# otherwise be taxed at 9% instead of 18% intra-state).
#
# Seller state (BRD 6.7.1): the company's own State from Company Settings.
# On a purchase the company is the buyer and the supplier the seller, but
# only "same state or not" matters to the engine, so the names are kept
# as the engine defines them.

import re
from decimal import Decimal

from hsn.models import HSN
from tax.models import Tax
from tenants.services import get_company_settings

ZERO = Decimal("0")


def _pct(value) -> Decimal:
    return Decimal(str(value)) if value else ZERO


def resolve_item_default_tax(item, tenant) -> Tax | None:
    """Item-level tax_type wins; falls back to the item's HSN row's
    tax_type when the item doesn't carry its own."""
    tax_code = (getattr(item, "tax_type", "") or "").strip()

    if not tax_code:
        hsn_code = (getattr(item, "hsn_code", "") or "").strip()
        if hsn_code:
            hsn = HSN.objects.filter(tenant=tenant, hsn_code=hsn_code).first()
            tax_code = (getattr(hsn, "tax_type", "") or "").strip() if hsn else ""

    return match_tax_code(tax_code, tenant)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9.]+", text.lower()))


def match_tax_code(tax_code: str, tenant) -> Tax | None:
    """Tax master row for a stored tax reference: a Tax id ("1"), or a label
    made of the tax's name and type in any order ("18% GST", "GST 18%").
    Unmatched text resolves to None rather than failing the request."""
    if not tax_code:
        return None
    taxes = Tax.objects.filter(tenant=tenant)
    if tax_code.isdigit():
        return taxes.filter(id=int(tax_code)).first()
    wanted = _tokens(tax_code)
    for tax in taxes.order_by("id"):
        if wanted == _tokens(f"{tax.tax_name} {tax.tax_type}"):
            return tax
    return None


def tax_rates(tax: Tax | None) -> dict:
    if tax is None:
        return {"intra": ZERO, "inter": ZERO, "cess": ZERO}
    split = _pct(tax.cgst_percentage) + _pct(tax.sgst_percentage)
    igst = _pct(tax.igst_percentage)
    return {
        "intra": split or igst,
        "inter": igst or split,
        "cess": _pct(tax.cess_percentage),
    }


def resolve_seller_state(tenant) -> str:
    return get_company_settings(tenant).state


def resolve_line_gst_inputs(tax: Tax | None, supplier, tenant) -> dict:
    """The inputs compute_line_tax needs for a line taxed with `tax` -
    the AUTHORITATIVE save-time calculation, independent of whatever the
    frontend's live preview showed."""
    seller_state = resolve_seller_state(tenant)
    buyer_state = getattr(supplier, "state", "") or ""
    rates = tax_rates(tax)
    intra = seller_state == buyer_state
    return {
        "gst_rate_percent": rates["intra"] if intra else rates["inter"],
        "cess_rate_percent": rates["cess"],
        "seller_state": seller_state,
        "buyer_state": buyer_state,
    }
