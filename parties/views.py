# parties/views.py

from django.db import models
from rest_framework import status, viewsets
from rest_framework.response import Response

from tenants.services import get_current_tenant, get_next_sequence_number

from .models import Party, PartySubTypeMaster
from .serializers import PartySerializer, PartySubTypeMasterSerializer
from django.db.models import Q                          # new
from rest_framework.exceptions import ValidationError   # new

PARTY_CODE_TARGET_LENGTH = 10
PARTY_KIND_FILTERS = {
    "customer": Q(is_customer=True) | Q(is_export_customer=True),
    "export_customer": Q(is_export_customer=True),
    "vendor": Q(is_vendor=True),
}

class PartyViewSet(viewsets.ModelViewSet):
    serializer_class = PartySerializer
    lookup_field = "party_code"  # one-line: URLs use /api/parties/C001/, matching the frontend's route param, not the
    # internal bigint id

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        qs = Party.objects.filter(tenant=tenant).prefetch_related(
            "contacts", "items", "bank_accounts", "shipping_addresses", "sub_types"
        )
        if self.action == "list":      # list only, so edit/delete by party_code still work for any party
            qs = self._filter_by_kind(qs)
        return qs

    def _filter_by_kind(self, qs):
        raw = self.request.query_params.get("kind", "")
        kinds = [k.strip().lower() for k in raw.split(",") if k.strip()]
        if not kinds:                  # no ?kind= -> exactly the old behaviour
            return qs
        unknown = [k for k in kinds if k not in PARTY_KIND_FILTERS]
        if unknown:
            raise ValidationError(
                {"kind": f"Unknown kind: {', '.join(unknown)}. Use: {', '.join(PARTY_KIND_FILTERS)}."}
            )
        combined = Q()
        for k in kinds:
            combined |= PARTY_KIND_FILTERS[k]
        return qs.filter(combined)
    
    def perform_create(self, serializer):
        # one-line: tenant is injected HERE, server-side — never accepted
        # from the request body. This is the concrete code behind the
        # "frontend never sends tenant_id" rule from earlier.
        tenant = get_current_tenant(self.request)
        party_code = serializer.validated_data.get("party_code", "")
        if len(party_code) < PARTY_CODE_TARGET_LENGTH:
            suffix = get_next_sequence_number(tenant, "PARTY", digits=PARTY_CODE_TARGET_LENGTH - len(party_code))
            party_code += suffix
        serializer.save(tenant=tenant, party_code=party_code)


class PartySubTypeMasterViewSet(viewsets.ModelViewSet):
    serializer_class = PartySubTypeMasterSerializer

    def get_queryset(self):
        # one-line: the two-tier lookup from the ERD design system
        # defaults (tenant=NULL) PLUS this tenant's own custom additions,
        # never another tenant's custom values.
        tenant = get_current_tenant(self.request)
        return PartySubTypeMaster.objects.filter(models.Q(tenant__isnull=True) | models.Q(tenant=tenant))

    def perform_create(self, serializer):
        # one-line: the "+ Add new type" action from the creatable-select
        # anything created through the normal API is tenant-owned, never a
        # system default. System defaults only get created via a data
        # migration or the Django admin directly, not this endpoint.
        tenant = get_current_tenant(self.request)
        serializer.save(tenant=tenant)
