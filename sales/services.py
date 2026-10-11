# sales/services.py
#
# Business rules shared by the sales serializers and views: stock
# postings, order status, amendments and document conversions.

import datetime
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from inventory.models import StockMovement
from inventory.services import StockLine, replace_document_movements, tracks_stock
from tenants.services import get_company_settings

from .models import (
    DeliveryChallan,
    SaleOrder,
    SalesInvoice,
    SalesReturn,
)

ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Stock
#
# Stock leaves when goods physically move: the challan if there is one,
# otherwise the invoice. A DC-based invoice never moves stock again and a
# proforma never moves stock. Sales may go below zero with a warning when
# Company Settings allow it (BRD 6.4.5); returns / receipts only add stock.
# ---------------------------------------------------------------------------

DC_DOC = "DeliveryChallan"
INVOICE_DOC = "SalesInvoice"
SALES_RETURN_DOC = "SalesReturn"
CHALLAN_RETURN_DOC = "ChallanReturn"


def post_challan_stock(challan: DeliveryChallan, created_by: str, *, remove: bool = False) -> list[str]:
    lines = (
        []
        if remove or challan.status == DeliveryChallan.STATUS_CANCELLED
        else [
            StockLine(line.item, line.godown, -line.quantity)
            for line in challan.items.select_related("item", "godown")
            if line.godown_id and tracks_stock(line.item)
        ]
    )
    return replace_document_movements(
        challan.tenant,
        doc_type=DC_DOC,
        doc_id=challan.id,
        doc_number=challan.dc_number,
        movement_type=StockMovement.TYPE_DC_OUT,
        movement_date=challan.dc_date,
        lines=lines,
        created_by=created_by,
        allow_negative=get_company_settings(challan.tenant).allow_negative_stock_on_sales,
    )


def post_invoice_stock(invoice: SalesInvoice, created_by: str, *, remove: bool = False) -> list[str]:
    moves_stock = invoice.invoice_type != SalesInvoice.TYPE_PROFORMA and invoice.status == SalesInvoice.STATUS_ACTIVE
    lines = (
        []
        if remove or not moves_stock
        else [
            StockLine(line.item, line.godown, -line.quantity)
            for line in invoice.items.select_related("item", "godown")
            if line.dc_item_id is None and line.godown_id and tracks_stock(line.item)
        ]
    )
    return replace_document_movements(
        invoice.tenant,
        doc_type=INVOICE_DOC,
        doc_id=invoice.id,
        doc_number=invoice.invoice_number,
        movement_type=StockMovement.TYPE_SALE,
        movement_date=invoice.invoice_date,
        lines=lines,
        created_by=created_by,
        allow_negative=get_company_settings(invoice.tenant).allow_negative_stock_on_sales,
    )


def post_sales_return_stock(sales_return: SalesReturn, created_by: str, *, remove: bool = False) -> None:
    lines = (
        []
        if remove or sales_return.status == SalesReturn.STATUS_CANCELLED
        else [
            StockLine(line.item, line.godown, line.quantity)
            for line in sales_return.items.select_related("item", "godown")
            if line.godown_id and tracks_stock(line.item)
        ]
    )
    replace_document_movements(
        sales_return.tenant,
        doc_type=SALES_RETURN_DOC,
        doc_id=sales_return.id,
        doc_number=sales_return.return_number,
        movement_type=StockMovement.TYPE_SALES_RETURN,
        movement_date=sales_return.return_date,
        lines=lines,
        created_by=created_by,
    )


def post_challan_return_stock(challan_return, created_by: str) -> None:
    lines = [
        StockLine(line.dc_item.item, line.godown or line.dc_item.godown, line.quantity)
        for line in challan_return.items.select_related("dc_item__item", "godown", "dc_item__godown")
        if (line.godown_id or line.dc_item.godown_id) and tracks_stock(line.dc_item.item)
    ]
    replace_document_movements(
        challan_return.delivery_challan.tenant,
        doc_type=CHALLAN_RETURN_DOC,
        doc_id=challan_return.id,
        doc_number=challan_return.delivery_challan.dc_number,
        movement_type=StockMovement.TYPE_DC_RETURN,
        movement_date=challan_return.return_date,
        lines=lines,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# Sale Order status and amendments
# ---------------------------------------------------------------------------


def refresh_sale_order_status(order: SaleOrder) -> None:
    """Pending until every line is fulfilled or short-closed, then Order
    Completed. A manually Closed order stays Closed."""
    if order.status == SaleOrder.STATUS_CLOSED:
        return
    lines = list(order.items.all())
    done = bool(lines) and all(line.pending_qty <= ZERO for line in lines)
    status = SaleOrder.STATUS_COMPLETED if done else SaleOrder.STATUS_PENDING
    if status != order.status:
        order.status = status
        order.save(update_fields=["status", "updated_at"])


def sale_order_has_fulfilment(order: SaleOrder) -> bool:
    return any(line.fulfilled_qty > ZERO for line in order.items.all())


def snapshot_sale_order(order: SaleOrder) -> dict:
    from .serializers import SaleOrderSerializer  # local: serializers import services

    return dict(SaleOrderSerializer(order).data)


def record_amendment(order: SaleOrder, *, reason: str, created_by: str) -> None:
    """Called before an amended order is saved: keeps the old version and
    moves the order to the next revision (same SO number)."""
    from .models import SaleOrderRevision

    if not reason.strip():
        raise ValidationError({"amendment_reason": "Give a reason for amending this order."})
    SaleOrderRevision.objects.create(
        sale_order=order,
        revision_no=order.revision_no,
        amendment_date=datetime.date.today(),
        reason=reason.strip(),
        snapshot=snapshot_sale_order(order),
        created_by=created_by,
    )
    order.revision_no += 1
    order.amendment_date = datetime.date.today()


def check_amended_lines(order: SaleOrder, new_lines: list[dict]) -> None:
    """An amendment may change prices and quantities but can't take a line
    below what has already been delivered / invoiced, nor drop a line that
    has been fulfilled."""
    kept_ids = {line.get("id") for line in new_lines if line.get("id")}
    for existing in order.items.all():
        fulfilled = existing.fulfilled_qty
        if fulfilled <= ZERO:
            continue
        if existing.id not in kept_ids:
            raise ValidationError(
                {
                    "items": f"'{existing.item}' has already been fulfilled ({fulfilled}) "
                    "and can't be removed from the order."
                }
            )
        new_qty = next(Decimal(str(line["quantity"])) for line in new_lines if line.get("id") == existing.id)
        if new_qty < fulfilled + existing.short_closed_qty:
            raise ValidationError({"items": f"'{existing.item}' can't go below {fulfilled} - already fulfilled."})


@transaction.atomic
def close_sale_order(order: SaleOrder, reason: str) -> None:
    if order.status == SaleOrder.STATUS_CLOSED:
        raise ValidationError("This order is already closed.")
    for line in order.items.all():
        pending = line.pending_qty
        if pending > ZERO:
            line.short_closed_qty += pending
            line.save(update_fields=["short_closed_qty"])
    order.status = SaleOrder.STATUS_CLOSED
    order.closed_reason = reason
    order.save(update_fields=["status", "closed_reason", "updated_at"])


@transaction.atomic
def short_close_lines(order: SaleOrder, line_ids: list[int]) -> None:
    lines = {line.id: line for line in order.items.all()}
    for line_id in line_ids:
        line = lines.get(line_id)
        if line is None:
            raise ValidationError({"lines": f"Line {line_id} is not on this order."})
        pending = line.pending_qty
        if pending > ZERO:
            line.short_closed_qty += pending
            line.save(update_fields=["short_closed_qty"])
    refresh_sale_order_status(order)


# ---------------------------------------------------------------------------
# Conversions - each returns the data for the target serializer, so the
# normal create path (numbering, pricing, validation, stock) is reused.
# ---------------------------------------------------------------------------


def _priced_line_data(line) -> dict:
    return {
        "item": line.item_id,
        "description": line.description,
        "uom": line.uom,
        "hsn_sac": line.hsn_sac,
        "quantity": str(line.quantity),
        "rate": str(line.rate),
        "discount_percent": str(line.discount_percent),
        "discount_amount": str(line.discount_amount),
        "tax": line.tax_id,
    }


def _header_data(source) -> dict:
    return {
        "customer": source.customer_id,
        "place_of_supply": source.place_of_supply,
        "price_includes_tax": source.price_includes_tax,
        "doc_discount_type": source.doc_discount_type,
        "doc_discount_value": str(source.doc_discount_value),
        "doc_discount_before_tax": source.doc_discount_before_tax,
        "round_off_enabled": source.round_off_enabled,
        "charges": [{"name": c.name, "amount": str(c.amount), "tax": c.tax_id} for c in source.charges.all()],
    }


def quotation_to_sale_order_data(quotation, today: datetime.date) -> dict:
    return {
        **_header_data(quotation),
        "so_date": today,
        "quotation": quotation.id,
        "items": [_priced_line_data(line) for line in quotation.items.all()],
    }


def quotation_to_invoice_data(quotation, today: datetime.date) -> dict:
    return {
        **_header_data(quotation),
        "invoice_date": today,
        "invoice_type": SalesInvoice.TYPE_DIRECT,
        "quotation": quotation.id,
        "items": [_priced_line_data(line) for line in quotation.items.all()],
    }


def sale_orders_to_invoice_data(orders: list[SaleOrder], today: datetime.date, godown_id) -> dict:
    """Order-based invoice for the pending lines of one or more orders of
    the SAME customer (BRD bulk convert is "customer based")."""
    first = orders[0]
    items = []
    for order in orders:
        for line in order.items.all():
            pending = line.pending_qty
            if pending > ZERO:
                items.append(
                    {**_priced_line_data(line), "quantity": str(pending), "so_item": line.id, "godown": godown_id}
                )
    if not items:
        raise ValidationError("Nothing is pending on the selected order(s).")
    data = {
        **_header_data(first),
        "invoice_date": today,
        "invoice_type": SalesInvoice.TYPE_EXPORT if first.so_type == SaleOrder.TYPE_EXPORT else SalesInvoice.TYPE_ORDER,
        "sale_order": first.id if len(orders) == 1 else None,
        "customer_po_no": first.customer_po_no,
        "customer_po_date": first.customer_po_date,
        "currency_code": first.currency_code,
        "exchange_rate": str(first.exchange_rate),
        # Advance is set off only on single-order invoices, where it can be
        # traced back to its order.
        "advance_adjusted": str(first.advance_balance if len(orders) == 1 else ZERO),
        "items": items,
    }
    return data
