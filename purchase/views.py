# purchase/views.py

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from tenants.services import get_current_tenant, get_next_sequence_number

from .models import PurchaseOrder, PurchaseRequisition
from .serializers import (
    PurchaseOrderApprovalSerializer,
    PurchaseOrderSerializer,
    PurchaseRequisitionApprovalSerializer,
    PurchaseRequisitionSerializer,
)

PR_NO_DIGITS = 3   # PR001
PO_NO_DIGITS = 3   # PO001


class PurchaseRequisitionViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseRequisitionSerializer
    lookup_field = "pr_no"

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        qs = PurchaseRequisition.objects.filter(tenant=tenant).prefetch_related("items")
        # one-line: supports the "Approved PRs surfaced in PO create" flow —
        # frontend calls GET /api/purchase-requisitions/?approval_status=Approved
        status_filter = self.request.query_params.get("approval_status")
        if status_filter:
            qs = qs.filter(approval_status=status_filter)
        return qs

    def perform_create(self, serializer):
        tenant = get_current_tenant(self.request)
        suffix = get_next_sequence_number(tenant, "PURCHASE_REQUISITION", digits=PR_NO_DIGITS)
        serializer.save(tenant=tenant, pr_no=f"PR{suffix}")

    @action(detail=True, methods=["post"])
    def approve(self, request, pr_no=None):
        pr = self.get_object()
        serializer = PurchaseRequisitionApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pr.approval_status = serializer.validated_data["approval_status"]
        pr.approved_by = serializer.validated_data["approved_by"]
        pr.reason = serializer.validated_data.get("reason", "")
        pr.save()
        return Response(PurchaseRequisitionSerializer(pr).data)


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseOrderSerializer
    lookup_field = "po_no"

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        return PurchaseOrder.objects.filter(tenant=tenant).prefetch_related("items", "terms")

    def perform_create(self, serializer):
        tenant = get_current_tenant(self.request)
        suffix = get_next_sequence_number(tenant, "PURCHASE_ORDER", digits=PO_NO_DIGITS)
        serializer.save(tenant=tenant, po_no=f"PO{suffix}")

    @action(detail=True, methods=["post"])
    def approve(self, request, po_no=None):
        po = self.get_object()
        serializer = PurchaseOrderApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        po.approval_status = serializer.validated_data["approval_status"]
        po.approved_by = serializer.validated_data["approved_by"]
        po.reason = serializer.validated_data.get("reason", "")
        po.save()
        return Response(PurchaseOrderSerializer(po).data)
