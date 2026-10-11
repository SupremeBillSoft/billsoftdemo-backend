# inventory/services.py
#
# The only code that writes StockMovement rows. Every write path locks the
# godowns involved (select_for_update) and refuses to leave any
# (item, godown) below zero - BRD 6.3.1 "prevent reducing stock below zero"
# and 6.6.1 "validate stock availability before transfer".

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from items.models import Item

from .models import Godown, StockMovement

PRODUCT = "Product"


def tracks_stock(item: Item) -> bool:
    # Services (e.g. freight) are billed but never stocked.
    return item.item_type == PRODUCT


def stock_on_hand(tenant, item_id: int, godown_id: int, exclude_doc: tuple[str, int] | None = None) -> Decimal:
    movements = StockMovement.objects.filter(tenant=tenant, item_id=item_id, godown_id=godown_id)
    if exclude_doc is not None:
        movements = movements.exclude(source_doc_type=exclude_doc[0], source_doc_id=exclude_doc[1])
    return movements.aggregate(total=Sum("quantity"))["total"] or Decimal("0")


@dataclass
class StockLine:
    item: Item
    godown: Godown
    quantity: Decimal  # signed
    batch_no: str = ""


def _lock_godowns(godown_ids) -> None:
    list(Godown.objects.select_for_update().filter(id__in=set(godown_ids)).order_by("id"))


def _write(tenant, line: StockLine, movement_type: str, movement_date, *, doc_type="", doc_id=None,
           doc_number="", remarks="", created_by="", exclude_doc=None) -> StockMovement:
    before = stock_on_hand(tenant, line.item.id, line.godown.id, exclude_doc=exclude_doc)
    return StockMovement.objects.create(
        tenant=tenant,
        item=line.item,
        godown=line.godown,
        movement_type=movement_type,
        movement_date=movement_date,
        quantity=line.quantity,
        batch_no=line.batch_no,
        source_doc_type=doc_type,
        source_doc_id=doc_id,
        source_doc_number=doc_number,
        balance_before=before,
        balance_after=before + line.quantity,
        remarks=remarks,
        created_by=created_by,
    )


def _ensure_not_negative(tenant, lines: list[StockLine], exclude_doc=None, *, warn_only: bool = False) -> list[str]:
    """Raise if any (item, godown) would go below zero - or, with
    warn_only, return the shortfalls as warnings and let the posting go
    ahead (sales may bill ahead of stock - BRD 6.4.5 3.1)."""
    warnings: list[str] = []
    delta: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    by_key: dict[tuple[int, int], StockLine] = {}
    for line in lines:
        key = (line.item.id, line.godown.id)
        delta[key] += line.quantity
        by_key[key] = line
    for key, change in delta.items():
        if change >= 0:
            continue
        available = stock_on_hand(tenant, key[0], key[1], exclude_doc=exclude_doc)
        if available + change < 0:
            line = by_key[key]
            text = (f"Not enough stock of '{line.item}' in godown '{line.godown}' - "
                    f"available {available}, needed {-change}.")
            if not warn_only:
                raise ValidationError({"stock": text})
            warnings.append(text + " Stock will go negative.")
    return warnings


@transaction.atomic
def replace_document_movements(tenant, *, doc_type: str, doc_id: int, doc_number: str, movement_type: str,
                               movement_date, lines: list[StockLine], created_by: str = "",
                               allow_negative: bool = False) -> list[str]:
    """Make a document's stock postings equal `lines` (pass [] to remove
    them, e.g. on cancel/delete). Validates the END state: removing a
    Purchase Invoice whose stock was already returned or transferred out
    is refused rather than driving the godown negative.

    allow_negative=True (sales documents, when Company Settings allow it)
    posts anyway and returns the shortfall warnings instead."""
    existing = list(StockMovement.objects.filter(tenant=tenant, source_doc_type=doc_type, source_doc_id=doc_id))
    _lock_godowns([m.godown_id for m in existing] + [line.godown.id for line in lines])

    # Net effect = new lines minus what the document posted before.
    net = [StockLine(m.item, m.godown, -m.quantity) for m in existing] + lines
    warnings = _ensure_not_negative(tenant, net, warn_only=allow_negative)

    StockMovement.objects.filter(id__in=[m.id for m in existing]).delete()
    for line in lines:
        _write(tenant, line, movement_type, movement_date, doc_type=doc_type, doc_id=doc_id,
               doc_number=doc_number, created_by=created_by)
    return warnings


@transaction.atomic
def adjust_stock(tenant, *, item: Item, godown: Godown, add: bool, quantity: Decimal, movement_date,
                 remarks: str = "", created_by: str = "") -> StockMovement:
    if quantity <= 0:
        raise ValidationError({"quantity": "Quantity must be greater than 0."})
    if not tracks_stock(item):
        raise ValidationError({"item": "Stock is only tracked for Product items, not Services."})
    _lock_godowns([godown.id])
    line = StockLine(item, godown, quantity if add else -quantity)
    _ensure_not_negative(tenant, [line])
    movement_type = StockMovement.TYPE_ADJUSTMENT_ADD if add else StockMovement.TYPE_ADJUSTMENT_REDUCE
    return _write(tenant, line, movement_type, movement_date, doc_type="StockAdjustment",
                  remarks=remarks, created_by=created_by)


@transaction.atomic
def post_transfer(tenant, transfer, created_by: str = "") -> None:
    lines_out = [StockLine(line.item, transfer.from_godown, -line.quantity) for line in transfer.items.all()]
    _lock_godowns([transfer.from_godown_id, transfer.to_godown_id])
    _ensure_not_negative(tenant, lines_out)
    number = transfer.transfer_number
    for out in lines_out:
        _write(tenant, out, StockMovement.TYPE_TRANSFER_OUT, transfer.transfer_date, doc_type="StockTransfer",
               doc_id=transfer.id, doc_number=number, remarks=transfer.remarks, created_by=created_by)
        _write(tenant, StockLine(out.item, transfer.to_godown, -out.quantity), StockMovement.TYPE_TRANSFER_IN,
               transfer.transfer_date, doc_type="StockTransfer", doc_id=transfer.id, doc_number=number,
               remarks=transfer.remarks, created_by=created_by)
