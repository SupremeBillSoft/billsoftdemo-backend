# common/gst/engine.py
#
# The shared GST calculation module, per architecture doc section 4 - kept
# deliberately separate from Sales/Purchase/POS. Pure functions, no DB
# access: every caller (PO, Purchase Invoice, Sales Invoice, POS, Returns,
# Notes) resolves the applicable GST%/CESS% from HSN/Tax Master itself and
# passes it in here, so this module never depends on any one master's
# schema and stays trivially unit-testable.

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class GstLineResult:
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal
    line_total: Decimal


def compute_line_tax(
    taxable_value: Decimal,
    gst_rate_percent: Decimal,
    cess_rate_percent: Decimal = Decimal("0"),
    *,
    seller_state: str,
    buyer_state: str,
    rcm_applicable: bool = False,
) -> GstLineResult:
    """Given a line's taxable value and the GST%/CESS% already resolved from
    HSN/Tax Master, returns the CGST/SGST/IGST/CESS split and line total.

    Place of supply: same state -> CGST+SGST (rate split evenly); different
    state -> IGST (full rate).

    RCM (reverse charge): the split is still computed and returned so it can
    be recorded on the document for GSTR reporting - this function does not
    decide whether the seller actually collects it from the buyer. That's a
    posting-time decision the caller makes with rcm_applicable, not a change
    to the math here.
    """
    intra_state = seller_state == buyer_state

    if intra_state:
        half_rate = gst_rate_percent / 2
        cgst = _round(taxable_value * half_rate / 100)
        sgst = _round(taxable_value * half_rate / 100)
        igst = Decimal("0.00")
    else:
        cgst = Decimal("0.00")
        sgst = Decimal("0.00")
        igst = _round(taxable_value * gst_rate_percent / 100)

    cess = _round(taxable_value * cess_rate_percent / 100)
    line_total = _round(taxable_value + cgst + sgst + igst + cess)

    return GstLineResult(
        taxable_value=_round(taxable_value),
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        cess=cess,
        line_total=line_total,
    )


@dataclass
class GstDocumentTotals:
    total_taxable: Decimal
    total_cgst: Decimal
    total_sgst: Decimal
    total_igst: Decimal
    total_cess: Decimal
    grand_total: Decimal


def compute_document_totals(lines: list[GstLineResult]) -> GstDocumentTotals:
    def sum_field(name: str) -> Decimal:
        return _round(sum((getattr(line, name) for line in lines), Decimal("0")))

    return GstDocumentTotals(
        total_taxable=sum_field("taxable_value"),
        total_cgst=sum_field("cgst"),
        total_sgst=sum_field("sgst"),
        total_igst=sum_field("igst"),
        total_cess=sum_field("cess"),
        grand_total=sum_field("line_total"),
    )
