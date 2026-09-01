# branch/views.py
from django.db import models as db_models
from rest_framework import viewsets

from tenants.services import get_current_tenant

from .models import Branch, BranchType
from .serializers import BranchSerializer, BranchTypeSerializer


class BranchViewSet(viewsets.ModelViewSet):
    serializer_class = BranchSerializer
    lookup_field = "branch_code"

    def get_queryset(self):
        return Branch.objects.filter(tenant=get_current_tenant(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))

class BranchTypeViewSet(viewsets.ModelViewSet):
    serializer_class = BranchTypeSerializer

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        return BranchType.objects.filter(db_models.Q(tenant__isnull=True) | db_models.Q(tenant=tenant))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))
