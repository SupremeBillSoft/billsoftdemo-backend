# sales/compliance/payload.py
#
# Builds the GST e-invoice JSON (INV-01 schema, version 1.1 - the format
# every IRP and GSP accepts) from a Sales Invoice or Credit Note. Kept
# separate from the provider so the same payload goes to whichever IRP /
# GSP is configured.

from decimal import Decimal

from tenants.services import get_company_settings

STATE_CODES = {
    "Jammu and Kashmir": "01",
    "Himachal Pradesh": "02",
    "Punjab": "03",
    "Chandigarh": "04",
    "Uttarakhand": "05",
    "Haryana": "06",
    "Delhi": "07",
    "Rajasthan": "08",
    "Uttar Pradesh": "09",
    "Bihar": "10",
    "Sikkim": "11",
    "Arunachal Pradesh": "12",
    "Nagaland": "13",
    "Manipur": "14",
    "Mizoram": "15",
    "Tripura": "16",
    "Meghalaya": "17",
    "Assam": "18",
    "West Bengal": "19",
    "Jharkhand": "20",
    "Odisha": "21",
    "Chhattisgarh": "22",
    "Madhya Pradesh": "23",
    "Gujarat": "24",
    "Dadra and Nagar Haveli and Daman and Diu": "26",
    "Maharashtra": "27",
    "Karnataka": "29",
    "Goa": "30",
    "Lakshadweep": "31",
    "Kerala": "32",
    "Tamil Nadu": "33",
    "Puducherry": "34",
    "Andaman and Nicobar Islands": "35",
    "Telangana": "36",
    "Andhra Pradesh": "37",
    "Ladakh": "38",
    "Other Territory": "97",
    "Other Country": "96",
}


def state_code(state: str, gstin: str = "") -> str:
    if gstin and len(gstin) >= 2 and gstin[:2].isdigit():
        return gstin[:2]
    return STATE_CODES.get((state or "").strip(), "96")


def _num(value) -> float:
    return float(Decimal(value or 0).quantize(Decimal("0.01")))


def _rate(line) -> float:
    taxable = Decimal(line.taxable_value or 0)
    tax = Decimal(line.cgst or 0) + Decimal(line.sgst or 0) + Decimal(line.igst or 0)
    return float((tax / taxable * 100).quantize(Decimal("0.01"))) if taxable else 0.0


def build_einvoice_payload(document, *, doc_type: str) -> dict:
    """doc_type: "INV" for a sales invoice, "CRN" for a credit note."""
    tenant = document.tenant
    company = get_company_settings(tenant)
    customer = document.customer
    invoice = document if doc_type == "INV" else document.sales_invoice
    is_export = invoice.invoice_type == invoice.TYPE_EXPORT
    if is_export:
        supply_type = "EXPWP" if invoice.export_with_igst else "EXPWOP"
    else:
        supply_type = "B2B"
    number = document.invoice_number if doc_type == "INV" else document.credit_note_number
    date = document.invoice_date if doc_type == "INV" else document.credit_note_date
    pos_state = document.place_of_supply or getattr(invoice, "ship_to_state", "") or customer.state

    items = []
    for index, line in enumerate(document.items.select_related("item").all(), start=1):
        items.append(
            {
                "SlNo": str(index),
                "PrdDesc": (line.description or line.item.item_name)[:300],
                "IsServc": "Y" if line.item.item_type == "Service" else "N",
                "HsnCd": line.hsn_sac or line.item.hsn_code or "",
                "Qty": float(line.quantity),
                "Unit": (line.uom or line.item.billing_uom or "OTH")[:8].upper(),
                "UnitPrice": _num(line.rate),
                "TotAmt": _num(Decimal(line.quantity) * Decimal(line.rate)),
                "Discount": _num(line.discount_amount),
                "AssAmt": _num(line.taxable_value),
                "GstRt": _rate(line),
                "IgstAmt": _num(line.igst),
                "CgstAmt": _num(line.cgst),
                "SgstAmt": _num(line.sgst),
                "CesAmt": _num(line.cess),
                "TotItemVal": _num(line.line_total),
            }
        )

    payload = {
        "Version": "1.1",
        "TranDtls": {"TaxSch": "GST", "SupTyp": supply_type, "RegRev": "N"},
        "DocDtls": {"Typ": doc_type, "No": number[:16], "Dt": date.strftime("%d/%m/%Y")},
        "SellerDtls": {
            "Gstin": company.gstin,
            "LglNm": company.business_name or tenant.tenant_name,
            "Addr1": company.address_1 or "-",
            "Loc": company.city or "-",
            "Pin": int(company.pincode) if (company.pincode or "").isdigit() else 999999,
            "Stcd": state_code(company.state, company.gstin),
        },
        "BuyerDtls": {
            "Gstin": "URP" if is_export else customer.gst_no,
            "LglNm": customer.party_name,
            "Pos": "96" if is_export else state_code(pos_state),
            "Addr1": customer.address_1 or "-",
            "Loc": customer.city or "-",
            "Pin": 999999 if is_export else (int(customer.pincode) if (customer.pincode or "").isdigit() else 999999),
            "Stcd": "96" if is_export else state_code(customer.state, customer.gst_no),
        },
        "ItemList": items,
        "ValDtls": {
            "AssVal": _num(document.total_taxable),
            "CgstVal": _num(document.total_cgst),
            "SgstVal": _num(document.total_sgst),
            "IgstVal": _num(document.total_igst),
            "CesVal": _num(document.total_cess),
            "Discount": _num(document.doc_discount_amount if not document.doc_discount_before_tax else 0),
            "OthChrg": _num(document.total_charges),
            "RndOffAmt": _num(document.round_off_amount),
            "TotInvVal": _num(document.net_amount),
        },
    }
    if doc_type == "CRN":
        payload["RefDtls"] = {
            "PrecDocDtls": [
                {
                    "InvNo": invoice.invoice_number,
                    "InvDt": invoice.invoice_date.strftime("%d/%m/%Y"),
                }
            ]
        }
    if is_export:
        payload["ExpDtls"] = {
            "ShipBNo": invoice.shipping_bill_no or None,
            "ShipBDt": invoice.shipping_bill_date.strftime("%d/%m/%Y") if invoice.shipping_bill_date else None,
            "Port": invoice.port_code or None,
            "ForCur": invoice.currency_code,
            "CntCode": "",
        }
    return payload
