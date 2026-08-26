# parties/views.py

from django.db import models
from rest_framework import status, viewsets
from rest_framework.response import Response

from tenants.services import get_current_tenant, get_next_sequence_number

from .models import Party, PartySubTypeMaster
from .serializers import PartySerializer, PartySubTypeMasterSerializer

PARTY_CODE_TARGET_LENGTH = 10

class PartyViewSet(viewsets.ModelViewSet):
    serializer_class = PartySerializer
    lookup_field = "party_code"  # one-line: URLs use /api/parties/C001/, matching the frontend's route param, not the
    # internal bigint id

    def get_queryset(self):
        # one-line: THE tenant-scoping enforcement point for every list/
        # retrieve/update/delete — no query in this viewset can ever see
        # another tenant's parties, because the queryset itself is filtered
        # before any row reaches the serializer.
        tenant = get_current_tenant(self.request)
        return Party.objects.filter(tenant=tenant).prefetch_related(
            "contacts", "items", "bank_accounts", "shipping_addresses", "sub_types"
        )

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
