# inventory/views.py

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from items.models import Item
from tenants.services import current_user_name, get_current_tenant

from .models import Godown, StockMovement, StockTransfer, StockTransferItem
from .serializers import (
    GodownSerializer,
    StockAdjustmentHistorySerializer,
    StockAdjustmentRequestSerializer,
    StockTransferSerializer,
)
from .services import adjust_stock, post_transfer


class TenantContextMixin:
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = get_current_tenant(self.request)
        return context


class GodownViewSet(TenantContextMixin, viewsets.ModelViewSet):
    serializer_class = GodownSerializer

    def get_queryset(self):
        return Godown.objects.filter(tenant=get_current_tenant(self.request))

    def _save(self, serializer):
        tenant = get_current_tenant(self.request)
        with transaction.atomic():
            # The first godown becomes the main one; marking another as main
            # moves the flag.
            is_first = not Godown.objects.filter(tenant=tenant).exists()
            godown = serializer.save(tenant=tenant, **({"is_main": True} if is_first else {}))
            if godown.is_main:
                Godown.objects.filter(tenant=tenant, is_main=True).exclude(pk=godown.pk).update(is_main=False)

    def perform_create(self, serializer):
        self._save(serializer)

    def perform_update(self, serializer):
        self._save(serializer)

    def perform_destroy(self, instance):
        # BRD 6.6.1 item 4: "restrict deletion if stock exists in the godown".
        # Any movement history also blocks it (the ledger references it).
        if instance.stock_movements.exists():
            raise ValidationError("This godown has stock history and cannot be deleted.")
        if instance.transfers_out.exists() or instance.transfers_in.exists():
            raise ValidationError("This godown is used on stock transfers and cannot be deleted.")
        instance.delete()


class StockListView(APIView):
    """GET /api/inventory/stock/?godown=&search=&item= - BRD 6.6.1 item 6.
    One row per (item, batch) for Product items, with stock quantity and
    value in the chosen godown (all godowns when none is given). Stock
    value = quantity x item purchase price (BRD 6.6.1 item 8)."""

    def get(self, request):
        tenant = get_current_tenant(request)
        items = Item.objects.filter(tenant=tenant, item_type="Product")
        search = request.query_params.get("search", "").strip()
        if search:
            items = items.filter(Q(item_name__icontains=search) | Q(item_code__icontains=search))
        item_id = request.query_params.get("item")
        if item_id:
            items = items.filter(id=item_id)

        movements = StockMovement.objects.filter(tenant=tenant, item__in=items)
        godown_id = request.query_params.get("godown")
        if godown_id:
            movements = movements.filter(godown_id=godown_id)
        totals = movements.values("item_id", "batch_no").annotate(qty=Sum("quantity"))
        qty_by_item: dict[int, list[tuple[str, Decimal]]] = defaultdict(list)
        for row in totals:
            qty_by_item[row["item_id"]].append((row["batch_no"], row["qty"]))

        rows = []
        for item in items.order_by("item_name"):
            purchase_price = Decimal(str(item.purchase_price or 0))
            for batch_no, qty in qty_by_item.get(item.id) or [("", Decimal("0"))]:
                rows.append({
                    "item": item.id,
                    "itemCode": item.item_code,
                    "itemName": item.item_name,
                    "uom": item.billing_uom,
                    "batchNo": batch_no,
                    "stockQty": str(qty),
                    "stockValue": str((qty * purchase_price).quantize(Decimal("0.01"))),
                    "sellingPrice": item.selling_price,
                    "purchasePrice": item.purchase_price,
                })
        return Response(rows)


class StockAdjustmentView(APIView):
    """POST /api/inventory/adjustments/ - the Item Master "Adjust Stock"
    popup. GET ?item=<id> - that item's adjustment audit history."""

    def get(self, request):
        tenant = get_current_tenant(request)
        history = StockMovement.objects.filter(
            tenant=tenant,
            movement_type__in=[StockMovement.TYPE_ADJUSTMENT_ADD, StockMovement.TYPE_ADJUSTMENT_REDUCE],
        ).select_related("godown")
        item_id = request.query_params.get("item")
        if item_id:
            history = history.filter(item_id=item_id)
        return Response(StockAdjustmentHistorySerializer(history, many=True).data)

    def post(self, request):
        tenant = get_current_tenant(request)
        payload = StockAdjustmentRequestSerializer(data=request.data, context={"tenant": tenant})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        movement = adjust_stock(
            tenant,
            item=data["item"],
            godown=data["godown"],
            add=data["adjustment_type"] == StockAdjustmentRequestSerializer.ADD,
            quantity=data["quantity"],
            movement_date=data["date"],
            remarks=data["remarks"],
            created_by=current_user_name(request),
        )
        return Response(StockAdjustmentHistorySerializer(movement).data, status=201)


class StockTransferViewSet(TenantContextMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Transfers are create-only: a posted transfer is part of the stock
    ledger, so it isn't edited or deleted (move stock back with another
    transfer instead)."""

    serializer_class = StockTransferSerializer

    def get_queryset(self):
        return (
            StockTransfer.objects.filter(tenant=get_current_tenant(self.request))
            .select_related("from_godown", "to_godown")
            .prefetch_related("items__item")
        )

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = get_current_tenant(self.request)
        user = current_user_name(self.request)
        items = serializer.validated_data.pop("items")
        transfer = serializer.save(tenant=tenant, created_by=user)
        StockTransferItem.objects.bulk_create([StockTransferItem(stock_transfer=transfer, **line) for line in items])
        post_transfer(tenant, transfer, created_by=user)
