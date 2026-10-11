# sales/compliance/actions.py
#
# Generate / cancel e-invoices (IRN) and e-way bills for sales documents,
# applying the GST rules in rules.py and talking to whichever provider is
# configured (providers.get_provider).

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from docnumbering.services import financial_year_start
from tenants.services import get_company_settings

from ..models import DeliveryChallan, EWayBill, SalesInvoice
from . import rules
from .payload import build_einvoice_payload
from .providers import ProviderError, get_provider


def _financial_year(document_date, fy_start_month: int) -> str:
    start = financial_year_start(document_date, fy_start_month)
    return f"{start}-{str(start + 1)[-2:]}"


def _provider_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ProviderError as exc:
        raise ValidationError(str(exc)) from exc


@transaction.atomic
def generate_einvoice(document) -> None:
    """document: a SalesInvoice or CreditNote with einvoice_status Pending."""
    is_invoice = isinstance(document, SalesInvoice)
    doc_type = "INV" if is_invoice else "CRN"
    doc_date = document.invoice_date if is_invoice else document.credit_note_date
    if document.status == document.STATUS_CANCELLED:
        raise ValidationError("A cancelled document can't be e-invoiced.")
    if document.einvoice_status == document.EINV_GENERATED:
        raise ValidationError("An IRN has already been generated for this document.")
    if document.einvoice_status != document.EINV_PENDING:
        raise ValidationError("E-invoicing doesn't apply to this document (B2C, proforma, or e-invoicing off).")
    settings = get_company_settings(document.tenant)
    problem = rules.einvoice_reporting_problem(
        document_date=doc_date, today=timezone.localdate(), aato_10cr_or_more=settings.aato_10cr_or_more
    )
    if problem:
        raise ValidationError(problem)

    payload = build_einvoice_payload(document, doc_type=doc_type)
    result = _provider_call(
        get_provider().generate_irn,
        payload,
        financial_year=_financial_year(doc_date, settings.financial_year_start_month),
    )
    document.irn = result.irn
    document.ack_no = result.ack_no
    document.ack_date = result.ack_date
    document.signed_qr = result.signed_qr
    document.einvoice_status = document.EINV_GENERATED
    document.save(update_fields=["irn", "ack_no", "ack_date", "signed_qr", "einvoice_status", "updated_at"])


@transaction.atomic
def cancel_einvoice(document, *, reason_code: str, remark: str) -> None:
    if document.einvoice_status != document.EINV_GENERATED:
        raise ValidationError("There is no active IRN to cancel.")
    if not rules.within_cancel_window(document.ack_date, timezone.now(), rules.EINVOICE_CANCEL_WINDOW):
        raise ValidationError(
            "An IRN can only be cancelled within 24 hours of generation - issue a Credit Note instead."
        )
    _provider_call(get_provider().cancel_irn, document.irn, reason_code=reason_code, remark=remark)
    document.einvoice_status = document.EINV_CANCELLED
    document.irn_cancelled_at = timezone.now()
    document.save(update_fields=["einvoice_status", "irn_cancelled_at", "updated_at"])


def eway_bill_check(document) -> dict:
    """Whether the document needs an e-way bill, and why - shown on screen
    before generating (one can always be generated voluntarily)."""
    settings = get_company_settings(document.tenant)
    is_invoice = isinstance(document, SalesInvoice)
    if is_invoice:
        value = document.net_amount
        buyer_state = document.place_of_supply or document.ship_to_state or document.customer.state
        job_work = False
    else:
        value = document.approx_value
        buyer_state = document.ship_to_state or document.customer.state
        job_work = document.purpose == DeliveryChallan.PURPOSE_JOB_WORK
    seller_state = settings.state
    inter_state = bool(seller_state) and seller_state != buyer_state
    threshold = rules.eway_bill_threshold(
        default=settings.eway_bill_threshold,
        intra_state_overrides=settings.eway_bill_intra_state_thresholds,
        seller_state=seller_state,
        buyer_state=buyer_state,
    )
    required, reason = rules.eway_bill_required(
        consignment_value=Decimal(value), threshold=threshold, inter_state=inter_state, job_work=job_work
    )
    return {"required": required, "reason": reason, "consignmentValue": str(value), "threshold": str(threshold)}


@transaction.atomic
def generate_eway_bill(document, data: dict, created_by: str) -> EWayBill:
    is_invoice = isinstance(document, SalesInvoice)
    doc_date = document.invoice_date if is_invoice else document.dc_date
    number = document.invoice_number if is_invoice else document.dc_number
    if document.status == document.STATUS_CANCELLED:
        raise ValidationError("A cancelled document can't have an e-way bill.")
    if is_invoice and document.invoice_type == SalesInvoice.TYPE_PROFORMA:
        raise ValidationError("A proforma invoice can't have an e-way bill.")
    if rules.eway_document_too_old(document_date=doc_date, today=timezone.localdate()):
        raise ValidationError("E-way bills can't be generated for documents more than 180 days old.")
    content_type = ContentType.objects.get_for_model(document)
    if EWayBill.objects.filter(
        content_type=content_type, object_id=document.id, status=EWayBill.STATUS_ACTIVE
    ).exists():
        raise ValidationError("This document already has an active e-way bill.")

    check = eway_bill_check(document)
    sub_type = "Supply"
    if not is_invoice and document.purpose == DeliveryChallan.PURPOSE_JOB_WORK:
        sub_type = "Job Work"
    elif is_invoice and document.is_export:
        sub_type = "Export"
    result = _provider_call(
        get_provider().generate_eway_bill,
        {
            **data,
            "document_number": number,
            "document_date": doc_date.isoformat(),
            "consignment_value": check["consignmentValue"],
            "sub_type": sub_type,
        },
    )
    valid_till = result.valid_till or rules.eway_bill_valid_till(
        generated_at=result.ewb_date,
        distance_km=data["distance_km"],
        over_dimensional=data.get("vehicle_type") == EWayBill.VEHICLE_ODC,
    )
    return EWayBill.objects.create(
        tenant=document.tenant,
        content_type=content_type,
        object_id=document.id,
        document_number=number,
        ewb_number=result.ewb_no,
        ewb_date=result.ewb_date,
        valid_till=valid_till,
        sub_type=sub_type,
        consignment_value=Decimal(check["consignmentValue"]),
        created_by=created_by,
        vehicle_history=[
            {"vehicleNo": data.get("vehicle_no", ""), "at": result.ewb_date.isoformat(), "reason": "Initial"}
        ]
        if data.get("vehicle_no")
        else [],
        **{
            k: v
            for k, v in data.items()
            if k
            in {
                "transport_mode",
                "vehicle_type",
                "vehicle_no",
                "transporter_id",
                "transporter_name",
                "transport_doc_no",
                "transport_doc_date",
                "distance_km",
            }
        },
    )


@transaction.atomic
def cancel_eway_bill(ewb: EWayBill, *, reason_code: str, remark: str) -> None:
    if ewb.status != EWayBill.STATUS_ACTIVE:
        raise ValidationError("This e-way bill is already cancelled.")
    if not rules.within_cancel_window(ewb.ewb_date, timezone.now(), rules.EWB_CANCEL_WINDOW):
        raise ValidationError("An e-way bill can only be cancelled within 24 hours of generation.")
    _provider_call(get_provider().cancel_eway_bill, ewb.ewb_number, reason_code=reason_code, remark=remark)
    ewb.status = EWayBill.STATUS_CANCELLED
    ewb.cancel_reason = remark
    ewb.cancelled_at = timezone.now()
    ewb.save(update_fields=["status", "cancel_reason", "cancelled_at"])


@transaction.atomic
def update_vehicle(ewb: EWayBill, *, vehicle_no: str, reason: str, place: str) -> None:
    """Part-B update (BRD "Update Vehicle") - allowed while valid."""
    if ewb.status != EWayBill.STATUS_ACTIVE:
        raise ValidationError("This e-way bill is cancelled.")
    if ewb.valid_till and ewb.valid_till < timezone.now():
        raise ValidationError("This e-way bill has expired.")
    _provider_call(get_provider().update_vehicle, ewb.ewb_number, vehicle_no=vehicle_no, reason=reason, place=place)
    ewb.vehicle_no = vehicle_no
    ewb.vehicle_history = [
        *ewb.vehicle_history,
        {"vehicleNo": vehicle_no, "reason": reason, "place": place, "at": timezone.now().isoformat()},
    ]
    ewb.save(update_fields=["vehicle_no", "vehicle_history"])
