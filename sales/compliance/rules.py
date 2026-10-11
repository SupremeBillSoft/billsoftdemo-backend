# sales/compliance/rules.py
#
# GST compliance rules used by e-invoicing and e-way bills. Pure functions
# so they are easy to test and to update when the law changes.
#
# Sources (checked Oct 2026):
#  - E-invoicing: B2B, exports, credit/debit notes once aggregate turnover
#    > Rs 5 crore; not B2C. IRN can be cancelled within 24 hours. From
#    1 Apr 2025, AATO >= Rs 10 crore must report within 30 days of the
#    document date (the IRP rejects older documents).
#  - E-way bill: consignment value > Rs 50,000 (states may set a higher
#    intra-state limit); inter-state job-work movement needs one whatever
#    the value. Validity 1 day per 200 km (1 per 20 km for over-dimensional
#    cargo). Cannot be generated for documents older than 180 days (from
#    1 Jan 2025). Cancellable within 24 hours.

import datetime
import math
from decimal import Decimal

EINVOICE_CANCEL_WINDOW = datetime.timedelta(hours=24)
EWB_CANCEL_WINDOW = datetime.timedelta(hours=24)
EINVOICE_REPORTING_DAYS = 30
EWB_MAX_DOCUMENT_AGE_DAYS = 180
EWB_KM_PER_DAY = 200
EWB_ODC_KM_PER_DAY = 20


def einvoice_applicable(*, enabled: bool, customer_gstin: str, is_export: bool, is_proforma: bool) -> bool:
    """B2B (customer has a GSTIN) and exports, when the company is under
    e-invoicing. Never B2C, never a proforma."""
    if not enabled or is_proforma:
        return False
    return is_export or bool((customer_gstin or "").strip())


def einvoice_reporting_problem(
    *, document_date: datetime.date, today: datetime.date, aato_10cr_or_more: bool
) -> str | None:
    if aato_10cr_or_more and (today - document_date).days > EINVOICE_REPORTING_DAYS:
        return (
            f"The IRP accepts documents up to {EINVOICE_REPORTING_DAYS} days old for turnover of Rs 10 crore "
            "and above - this document is too old to report."
        )
    return None


def within_cancel_window(generated_at: datetime.datetime, now: datetime.datetime, window: datetime.timedelta) -> bool:
    return now - generated_at <= window


def eway_bill_threshold(
    *, default: Decimal, intra_state_overrides: dict, seller_state: str, buyer_state: str
) -> Decimal:
    if seller_state and seller_state == buyer_state and seller_state in (intra_state_overrides or {}):
        return Decimal(str(intra_state_overrides[seller_state]))
    return default


def eway_bill_required(
    *, consignment_value: Decimal, threshold: Decimal, inter_state: bool, job_work: bool
) -> tuple[bool, str]:
    if job_work and inter_state:
        return True, "Inter-state movement for job work needs an e-way bill whatever the value."
    if consignment_value > threshold:
        return True, f"Consignment value {consignment_value} is above the e-way bill limit of {threshold}."
    return False, f"Consignment value {consignment_value} is within the e-way bill limit of {threshold}."


def eway_bill_valid_till(
    *, generated_at: datetime.datetime, distance_km: int, over_dimensional: bool
) -> datetime.datetime:
    """Validity ends at midnight of the last day: day 1 runs to midnight of
    the day after generation, each further block of km adds a day."""
    per_day = EWB_ODC_KM_PER_DAY if over_dimensional else EWB_KM_PER_DAY
    days = max(1, math.ceil(max(distance_km, 1) / per_day))
    last_day = generated_at.date() + datetime.timedelta(days=days)
    return datetime.datetime.combine(last_day, datetime.time(23, 59, 59), tzinfo=generated_at.tzinfo)


def eway_document_too_old(*, document_date: datetime.date, today: datetime.date) -> bool:
    return (today - document_date).days > EWB_MAX_DOCUMENT_AGE_DAYS


def credit_note_deadline(*, invoice_date: datetime.date, fy_start_month: int) -> datetime.date:
    """CGST Act s.34(2): declare a credit note by 30 November following the
    end of the financial year of the original supply. (The other limit -
    the annual return date, if earlier - is checked when filing.)"""
    fy_start_year = invoice_date.year if invoice_date.month >= fy_start_month else invoice_date.year - 1
    fy_end_month = (fy_start_month - 2) % 12 + 1  # month before the FY starts
    fy_end_year = fy_start_year + (1 if fy_start_month > 1 else 0)
    # The first 30 November after the financial year ends.
    return datetime.date(fy_end_year if fy_end_month < 11 else fy_end_year + 1, 11, 30)


def job_work_return_due(*, sent_on: datetime.date, capital_goods: bool) -> datetime.date:
    """CGST Act s.143: inputs back within 1 year, capital goods within 3."""
    years = 3 if capital_goods else 1
    try:
        return sent_on.replace(year=sent_on.year + years)
    except ValueError:  # 29 Feb
        return sent_on.replace(year=sent_on.year + years, day=28)
