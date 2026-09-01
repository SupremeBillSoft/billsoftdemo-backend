# hsn/views.py
from django.db import models as db_models
from rest_framework import viewsets

from tenants.services import get_current_tenant

from .models import HSN, HSNItemType, HSNProductCategory
from .serializers import HSNItemTypeSerializer, HSNProductCategorySerializer, HSNSerializer


class HSNViewSet(viewsets.ModelViewSet):
    serializer_class = HSNSerializer
    lookup_field = "hsn_code"

    def get_queryset(self):
        return HSN.objects.filter(tenant=get_current_tenant(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))


class HSNItemTypeViewSet(viewsets.ModelViewSet):
    serializer_class = HSNItemTypeSerializer

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        return HSNItemType.objects.filter(db_models.Q(tenant__isnull=True) | db_models.Q(tenant=tenant))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))


class HSNProductCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = HSNProductCategorySerializer

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        return HSNProductCategory.objects.filter(db_models.Q(tenant__isnull=True) | db_models.Q(tenant=tenant))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))
