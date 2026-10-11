# docnumbering/services.py
#
# The functions every other module actually calls:
#   - preview(fmt)         -> read-only "Final Doc Format" live preview, used
#                              by the settings screen as the admin edits.
#   - next_number(...)     -> the ONLY way a real document number is issued.
#                              Locks the active format row, increments its
#                              counter, and returns the formatted string.
#                              Never call this speculatively for a preview -
#                              use preview() for that, since next_number()
#                              has a permanent side effect (the counter moves).
#
# ASSEMBLY RULE (confirmed: "based on the values entered in the input box").
# The number is built purely from the row's fields, in this slot order:
#
#     Prefix [PrefixSep] First [DocNoSep] MidText [MidTextSep] Second [SuffixSep] Suffix
#
#   - First/Second are the Doc No and the Year; "Doc No. Display Order"
#     (is_year_first) decides which comes first.
#   - Doc No is zero-padded to Format Digit.
#   - Year = Fin Year 1 [YearSep] Fin Year 2, each 2 or 4 digits, for the
#     financial year the format's From Date falls in.
#   - A separator is only written when it was selected AND there is a part on
#     both sides of it - nothing is ever left dangling at either end.
#
#   e.g. Prefix "P", PrefixSep "/", 1 digit, DocNoSep "/", FY 2+2 "-" -> P/1/24-25
#        Prefix "P", no separators, year first, 5 digits, FY 2+2      -> P242500001

from __future__ import annotations

import datetime

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import DocumentNumberFormat

DEFAULT_FY_START_MONTH = 4  # April - Indian GST financial year


class MissingNumberFormat(ValidationError):
    """No active format for a document - surfaces as a 400 with a message
    telling the admin what to set up, not a 500."""


def financial_year_start(day: datetime.date, fy_start_month: int) -> int:
    """The calendar year the financial year containing `day` started in."""
    return day.year if day.month >= fy_start_month else day.year - 1


def _fy_text(year: int, digits: int | None) -> str:
    return str(year)[-2:] if digits == 2 else str(year)


def _fy_start_month_for(fmt: DocumentNumberFormat) -> int:
    from tenants.services import get_company_settings  # local import: tenants doesn't depend on docnumbering

    return get_company_settings(fmt.tenant).financial_year_start_month or DEFAULT_FY_START_MONTH


def build_number(fmt: DocumentNumberFormat, number: int, fy_start_month: int) -> str:
    fy_start = financial_year_start(fmt.from_date, fy_start_month)
    year_parts = []
    if fmt.fin_year_1:
        year_parts.append(_fy_text(fy_start, fmt.fin_year_1))
    if fmt.fin_year_2:
        year_parts.append(_fy_text(fy_start + 1, fmt.fin_year_2))
    year = fmt.year_separator.join(year_parts)
    doc_no = str(number).zfill(fmt.format_digit or 1)

    first, second = (year, doc_no) if fmt.is_year_first else (doc_no, year)
    slots = [
        (fmt.prefix, fmt.prefix_separator),
        (first, fmt.doc_separator),
        (fmt.mid_text, fmt.mid_text_separator),
        (second, ""),
    ]

    result = ""
    pending_separator = ""
    for text, separator_after in slots:
        if not text:
            continue
        result = f"{result}{pending_separator}{text}" if result else text
        pending_separator = separator_after

    if fmt.suffix:
        result = f"{result}{fmt.suffix_separator}{fmt.suffix}" if result else fmt.suffix
    return result


def preview(fmt: DocumentNumberFormat, doc_no: int | None = None, fy_start_month: int | None = None) -> str:
    """The number this format would issue next (or for `doc_no`)."""
    return build_number(
        fmt,
        fmt.doc_no if doc_no is None else doc_no,
        fy_start_month or _fy_start_month_for(fmt),
    )


def _classification_candidates(doc_type: str, doc_sub_type: str) -> list[tuple[str, str]]:
    # Most specific first: a document may have its own series per type /
    # sub-type (e.g. Invoice / Regular / Labour), otherwise it falls back to
    # the document's general series.
    candidates = [(doc_type, doc_sub_type), (doc_type, ""), ("", "")]
    return list(dict.fromkeys(candidates))


def get_active_format(tenant, doc: str, doc_type: str = "", doc_sub_type: str = "", *, lock: bool = False):
    formats = DocumentNumberFormat.objects.filter(tenant=tenant, doc=doc, to_date__isnull=True, is_active=True)
    if lock:
        formats = formats.select_for_update()
    for candidate_type, candidate_sub_type in _classification_candidates(doc_type, doc_sub_type):
        fmt = formats.filter(doc_type=candidate_type, doc_sub_type=candidate_sub_type).first()
        if fmt is not None:
            return fmt
    classification = " / ".join(part for part in [doc, doc_type, doc_sub_type] if part)
    raise MissingNumberFormat(
        f"No active Document Number Format for '{classification}'. "
        f"Set one up in Settings > Document Numbering (Document '{doc}')."
    )


@transaction.atomic
def next_number(tenant, doc: str, doc_type: str = "", doc_sub_type: str = "") -> str:
    """Issue the next number for (tenant, doc, doc_type, doc_sub_type),
    falling back to (doc, doc_type) and then (doc) when no more specific
    series exists. Locks the format row for the duration of the
    transaction so two concurrent saves can never receive the same number."""
    if not DocumentNumberFormat.objects.filter(tenant=tenant, doc=doc).exists():
        # A document that has never been configured gets its default format
        # on first use rather than every save failing.
        seed_default_formats_for_tenant(tenant, datetime.date.today(), only_doc=doc)

    fmt = get_active_format(tenant, doc, doc_type, doc_sub_type, lock=True)
    issued_number = fmt.doc_no
    fmt.doc_no += 1
    fmt.issued_count += 1
    fmt.save(update_fields=["doc_no", "issued_count", "updated_at"])
    return preview(fmt, doc_no=issued_number)


@transaction.atomic
def start_new_version(
    current: DocumentNumberFormat, from_date, reset_doc_no: bool = True
) -> DocumentNumberFormat:
    """Closes `current` and opens a new format version carrying forward every
    field except from_date/to_date/doc_no. This is the manual equivalent of
    what the FY-rollover Celery task (tasks.py) does automatically - used
    both by that task and by the settings screen's "Start New Version"
    action, so the two paths can never drift apart."""
    if current.to_date is not None:
        raise ValueError("Cannot start a new version from an already-closed format row.")

    current.to_date = from_date
    current.save(update_fields=["to_date", "updated_at"])

    return DocumentNumberFormat.objects.create(
        tenant=current.tenant,
        doc=current.doc,
        doc_type=current.doc_type,
        doc_sub_type=current.doc_sub_type,
        prefix=current.prefix,
        prefix_separator=current.prefix_separator,
        mid_text=current.mid_text,
        mid_text_separator=current.mid_text_separator,
        suffix=current.suffix,
        suffix_separator=current.suffix_separator,
        doc_no=1 if reset_doc_no else current.doc_no,
        format_digit=current.format_digit,
        doc_separator=current.doc_separator,
        fin_year_1=current.fin_year_1,
        fin_year_2=current.fin_year_2,
        year_separator=current.year_separator,
        is_year_first=current.is_year_first,
        from_date=from_date,
        to_date=None,
        is_active=True,
    )


# Document codes the purchase module issues numbers for - must match the
# `doc=` each serializer passes to next_number().
DEFAULT_FORMATS = [
    {"doc": "PR", "prefix": "PR"},
    {"doc": "PO", "prefix": "PO"},
    {"doc": "PI", "prefix": "PI"},
    {"doc": "PRET", "prefix": "PRET"},
    {"doc": "DN", "prefix": "DN"},
    # Sales - document codes follow the legacy Doc_Number_Format names
    # (Invoice, InvoiceProforma, DC, CreditNote) so imported rows just work.
    {"doc": "Enquiry", "prefix": "ENQ"},
    {"doc": "Quotation", "prefix": "QTN"},
    {"doc": "SO", "prefix": "SO"},
    {"doc": "DC", "prefix": "DC"},
    {"doc": "Invoice", "prefix": "INV"},
    {"doc": "InvoiceProforma", "prefix": "PRO"},
    {"doc": "SalesReturn", "prefix": "SRET"},
    {"doc": "CreditNote", "prefix": "CN"},
]


def seed_default_formats_for_tenant(tenant, today, only_doc: str | None = None) -> None:
    """Gives a tenant a working format for each purchase document (e.g.
    PO/0001/26-27), starting at the current financial year. The admin can
    refine them in Settings > Document Numbering at any time."""
    from tenants.services import get_company_settings

    fy_start_month = get_company_settings(tenant).financial_year_start_month or DEFAULT_FY_START_MONTH
    fy_start = datetime.date(financial_year_start(today, fy_start_month), fy_start_month, 1)
    for entry in DEFAULT_FORMATS:
        if only_doc is not None and entry["doc"] != only_doc:
            continue
        DocumentNumberFormat.objects.get_or_create(
            tenant=tenant,
            doc=entry["doc"],
            doc_type="",
            doc_sub_type="",
            to_date=None,
            defaults={
                "prefix": entry["prefix"],
                "prefix_separator": "/",
                "format_digit": 4,
                "doc_separator": "/",
                "fin_year_1": 2,
                "fin_year_2": 2,
                "year_separator": "-",
                "doc_no": 1,
                "from_date": fy_start,
            },
        )
