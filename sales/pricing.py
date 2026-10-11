# sales/pricing.py
#
# The one calculation every sales document uses (Quotation, Sale Order,
# Invoice, Return, Credit Note). Pure apart from tax / state lookups.
#
# Per line (BRD 6.4.3.2 3.3):
#   gross    = qty x rate                       (0 for a free / BOGO item)
#   discount = discount % of gross, or the amount given (each derived from
#              the other)
#   taxable  = gross - discount, or - when rates include tax - backed out:
#              (gross - discount) / (1 + (GST% + CESS%) / 100)
# Document discount BEFORE tax is shared across lines in proportion to
# their taxable value and reduces it - recorded on the invoice, so excluded
# from the value of supply (CGST Act s.15(3)(a)). AFTER tax it only reduces
# the amount payable. Additional charges carry their own tax. Totals are
# rounded to the rupee when round-off is on; a manual adjustment is added
# last.
#
# GST rate comes from the line's Tax master row (user-chosen, else the
# item's default): CGST+SGST when the place of supply is the company's own
# state, IGST otherwise. Exports are always inter-state; under LUT they are
# zero-rated (rate 0, "EXPWOP").

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from common.gst.engine import compute_line_tax
from purchase.gst_resolution import resolve_item_default_tax, resolve_seller_state, tax_rates

ZERO = Decimal("0")
HUNDRED = Decimal("100")
EXPORT_STATE = "OUTSIDE INDIA"


def money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else ZERO


@dataclass
class PricingOptions:
    buyer_state: str
    price_includes_tax: bool = False
    doc_discount_type: str = "Percent"
    doc_discount_value: Decimal = ZERO
    doc_discount_before_tax: bool = True
    round_off_enabled: bool = True
    adjustment_amount: Decimal = ZERO
    export: bool = False
    zero_rated: bool = False  # export under LUT


@dataclass
class PricedResult:
    lines: list[dict] = field(default_factory=list)
    charges: list[dict] = field(default_factory=list)
    totals: dict = field(default_factory=dict)


def _rate_for(tax, intra: bool, options: PricingOptions) -> tuple[Decimal, Decimal]:
    if options.zero_rated:
        return ZERO, ZERO
    rates = tax_rates(tax)
    return (rates["intra"] if intra else rates["inter"]), rates["cess"]


def price_document(tenant, lines: list[dict], charges: list[dict], options: PricingOptions) -> PricedResult:
    """`lines`: dicts with item, quantity, rate and optional
    discount_percent / discount_amount / tax / is_free (other keys pass
    through). `charges`: dicts with name, amount and optional tax.
    Returns the lines and charges with every computed field filled in, plus
    the document totals."""
    seller_state = resolve_seller_state(tenant)
    buyer_state = EXPORT_STATE if options.export else (options.buyer_state or "")
    intra = seller_state == buyer_state and not options.export

    # 1. Line gross, line discount, taxable (before any document discount).
    work = []
    for raw in lines:
        line = dict(raw)
        tax = line.get("tax") or resolve_item_default_tax(line["item"], tenant)
        gst_rate, cess_rate = _rate_for(tax, intra, options)
        gross = ZERO if line.get("is_free") else _dec(line["quantity"]) * _dec(line["rate"])

        percent = _dec(line.get("discount_percent"))
        amount = _dec(line.get("discount_amount"))
        if percent:
            amount = money(gross * percent / HUNDRED)
        elif amount and gross:
            percent = (amount / gross * HUNDRED).quantize(Decimal("0.01"))
        net = gross - amount

        taxable = net / (1 + (gst_rate + cess_rate) / HUNDRED) if options.price_includes_tax else net
        line.update(tax=tax, discount_percent=percent, discount_amount=money(amount))
        work.append({"line": line, "taxable": taxable, "gst_rate": gst_rate, "cess_rate": cess_rate})

    # 2. Document discount before tax: apportion by taxable value.
    base = sum((w["taxable"] for w in work), ZERO)
    doc_discount = ZERO
    if options.doc_discount_value:
        if options.doc_discount_type == "Percent":
            doc_discount = money(base * _dec(options.doc_discount_value) / HUNDRED)
        else:
            doc_discount = money(_dec(options.doc_discount_value))
    before_tax_discount = doc_discount if options.doc_discount_before_tax else ZERO
    if before_tax_discount and base:
        remaining = before_tax_discount
        for index, w in enumerate(work):
            share = remaining if index == len(work) - 1 else money(before_tax_discount * w["taxable"] / base)
            w["taxable"] -= share
            remaining -= share

    # 3. GST per line.
    priced_lines = []
    for w in work:
        result = compute_line_tax(
            taxable_value=max(w["taxable"], ZERO),
            gst_rate_percent=w["gst_rate"],
            cess_rate_percent=w["cess_rate"],
            seller_state=seller_state,
            buyer_state=buyer_state,
        )
        priced_lines.append(
            {
                **w["line"],
                "taxable_value": result.taxable_value,
                "cgst": result.cgst,
                "sgst": result.sgst,
                "igst": result.igst,
                "cess": result.cess,
                "line_total": result.line_total,
            }
        )

    # 4. Additional charges, each with its own tax applicability.
    priced_charges = []
    for raw in charges:
        charge = dict(raw)
        gst_rate, cess_rate = _rate_for(charge.get("tax"), intra, options) if charge.get("tax") else (ZERO, ZERO)
        result = compute_line_tax(
            taxable_value=_dec(charge["amount"]),
            gst_rate_percent=gst_rate,
            cess_rate_percent=cess_rate,
            seller_state=seller_state,
            buyer_state=buyer_state,
        )
        charge.update(
            amount=result.taxable_value,
            cgst=result.cgst,
            sgst=result.sgst,
            igst=result.igst,
            cess=result.cess,
            total=result.line_total,
        )
        priced_charges.append(charge)

    # 5. Totals.
    def total_of(key, rows):
        return sum((r[key] for r in rows), ZERO)

    total_taxable = total_of("taxable_value", priced_lines) + total_of("amount", priced_charges)
    totals = {
        "total_taxable": money(total_taxable),
        "total_cgst": money(total_of("cgst", priced_lines) + total_of("cgst", priced_charges)),
        "total_sgst": money(total_of("sgst", priced_lines) + total_of("sgst", priced_charges)),
        "total_igst": money(total_of("igst", priced_lines) + total_of("igst", priced_charges)),
        "total_cess": money(total_of("cess", priced_lines) + total_of("cess", priced_charges)),
        "total_charges": money(total_of("total", priced_charges)),
        "doc_discount_amount": doc_discount,
    }
    gross_total = total_of("line_total", priced_lines) + total_of("total", priced_charges)
    after_tax_discount = ZERO if options.doc_discount_before_tax else doc_discount
    before_round = gross_total - after_tax_discount + _dec(options.adjustment_amount)
    rounded = before_round.quantize(Decimal("1"), rounding=ROUND_HALF_UP) if options.round_off_enabled else before_round
    totals["round_off_amount"] = money(rounded - before_round)
    totals["net_amount"] = money(rounded)
    return PricedResult(lines=priced_lines, charges=priced_charges, totals=totals)
