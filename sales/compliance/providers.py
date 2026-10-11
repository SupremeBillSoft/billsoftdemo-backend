# sales/compliance/providers.py
#
# One interface for talking to the government systems (IRP for e-invoice,
# NIC for e-way bill), so the app is not tied to a single vendor:
#
#   EInvoiceProvider            - the contract the rest of the app uses
#   SandboxProvider             - built-in stand-in for development and
#                                 tests; produces realistic IRNs (the real
#                                 SHA-256 formula) without calling anyone
#   GspProvider                 - skeleton for a GST Suvidha Provider /
#                                 direct IRP integration; fill in once the
#                                 provider and credentials are chosen
#
# Selected with the EINVOICE_PROVIDER setting ("sandbox" by default).
# Credentials come from environment variables, never the database.

import datetime
import hashlib
import json
import os
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone


class ProviderError(Exception):
    """The provider refused the request - the message is shown to the user."""


@dataclass
class IrnResult:
    irn: str
    ack_no: str
    ack_date: datetime.datetime
    signed_qr: str
    ewb_no: str = ""
    ewb_valid_till: datetime.datetime | None = None


@dataclass
class EwbResult:
    ewb_no: str
    ewb_date: datetime.datetime
    valid_till: datetime.datetime | None


class EInvoiceProvider:
    def generate_irn(self, payload: dict, *, financial_year: str) -> IrnResult:
        raise NotImplementedError

    def cancel_irn(self, irn: str, *, reason_code: str, remark: str) -> None:
        raise NotImplementedError

    def generate_eway_bill(self, data: dict) -> EwbResult:
        raise NotImplementedError

    def cancel_eway_bill(self, ewb_no: str, *, reason_code: str, remark: str) -> None:
        raise NotImplementedError

    def update_vehicle(self, ewb_no: str, *, vehicle_no: str, reason: str, place: str) -> None:
        raise NotImplementedError


def compute_irn(*, seller_gstin: str, financial_year: str, doc_type: str, doc_number: str) -> str:
    """The IRN formula the IRP itself uses: SHA-256 of supplier GSTIN +
    financial year + document type + document number."""
    raw = f"{seller_gstin}{financial_year}{doc_type}{doc_number}".upper()
    return hashlib.sha256(raw.encode()).hexdigest()


class SandboxProvider(EInvoiceProvider):
    """No network calls: returns the IRN the IRP would compute, plus made-up
    acknowledgement / e-way bill numbers. Rejects obviously invalid
    payloads the way the IRP would, so validation paths can be tested."""

    def generate_irn(self, payload: dict, *, financial_year: str) -> IrnResult:
        seller_gstin = payload["SellerDtls"].get("Gstin") or ""
        if len(seller_gstin) != 15:
            raise ProviderError("Company GSTIN is missing or invalid - set it in Company Settings.")
        buyer = payload["BuyerDtls"]
        if payload["TranDtls"]["SupTyp"] == "B2B" and len(buyer.get("Gstin") or "") != 15:
            raise ProviderError("Customer GSTIN is missing or invalid for a B2B e-invoice.")
        if not payload["ItemList"]:
            raise ProviderError("The document has no items.")
        doc = payload["DocDtls"]
        irn = compute_irn(
            seller_gstin=seller_gstin, financial_year=financial_year, doc_type=doc["Typ"], doc_number=doc["No"]
        )
        now = timezone.now()
        ack_no = str(int(irn[:12], 16))[:15]
        qr = json.dumps(
            {
                "SellerGstin": seller_gstin,
                "BuyerGstin": buyer.get("Gstin"),
                "DocNo": doc["No"],
                "DocTyp": doc["Typ"],
                "DocDt": doc["Dt"],
                "TotInvVal": payload["ValDtls"]["TotInvVal"],
                "Irn": irn,
                "IrnDt": now.isoformat(),
            }
        )
        return IrnResult(irn=irn, ack_no=ack_no, ack_date=now, signed_qr=qr)

    def cancel_irn(self, irn: str, *, reason_code: str, remark: str) -> None:
        if not irn:
            raise ProviderError("No IRN to cancel.")

    def generate_eway_bill(self, data: dict) -> EwbResult:
        if not data.get("distance_km"):
            raise ProviderError("Approximate distance is required for an e-way bill.")
        if data.get("transport_mode") == "Road" and not data.get("vehicle_no") and not data.get("transporter_id"):
            raise ProviderError("Give either the vehicle number or the transporter GSTIN.")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
        return EwbResult(ewb_no=str(int(digest[:10], 16))[:12].rjust(12, "1"), ewb_date=timezone.now(), valid_till=None)

    def cancel_eway_bill(self, ewb_no: str, *, reason_code: str, remark: str) -> None:
        if not ewb_no:
            raise ProviderError("No e-way bill to cancel.")

    def update_vehicle(self, ewb_no: str, *, vehicle_no: str, reason: str, place: str) -> None:
        if not vehicle_no:
            raise ProviderError("Vehicle number is required.")


class GspProvider(EInvoiceProvider):
    """Skeleton for the production integration through a GSP (or direct IRP
    API access). Each method maps 1:1 to the provider's documented API:
    authenticate (token), generate IRN, cancel IRN, generate / cancel EWB,
    update Part-B. Base URL and credentials are read from environment
    variables (EINVOICE_GSP_BASE_URL, EINVOICE_GSP_CLIENT_ID,
    EINVOICE_GSP_CLIENT_SECRET, EINVOICE_GSP_USERNAME, EINVOICE_GSP_PASSWORD).
    Implement against the chosen provider's sandbox first."""

    def __init__(self):
        self.base_url = os.environ.get("EINVOICE_GSP_BASE_URL", "")
        if not self.base_url:
            raise ProviderError("E-invoice provider is not configured (EINVOICE_GSP_BASE_URL missing).")

    def _not_ready(self):
        raise ProviderError("The live GSP integration has not been implemented yet - use the sandbox provider.")

    def generate_irn(self, payload: dict, *, financial_year: str) -> IrnResult:
        self._not_ready()

    def cancel_irn(self, irn: str, *, reason_code: str, remark: str) -> None:
        self._not_ready()

    def generate_eway_bill(self, data: dict) -> EwbResult:
        self._not_ready()

    def cancel_eway_bill(self, ewb_no: str, *, reason_code: str, remark: str) -> None:
        self._not_ready()

    def update_vehicle(self, ewb_no: str, *, vehicle_no: str, reason: str, place: str) -> None:
        self._not_ready()


def get_provider() -> EInvoiceProvider:
    name = getattr(settings, "EINVOICE_PROVIDER", "sandbox")
    if name == "gsp":
        return GspProvider()
    return SandboxProvider()
