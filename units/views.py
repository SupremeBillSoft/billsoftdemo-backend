# units/views.py

from rest_framework import viewsets

from tenants.services import get_current_tenant

from .models import Unit
from .serializers import UnitSerializer


class UnitViewSet(viewsets.ModelViewSet):
    serializer_class = UnitSerializer
    lookup_field = "unit_code"  # matches the frontend's :unitCode param, same convention as Party/Item

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        return Unit.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        tenant = get_current_tenant(self.request)
        serializer.save(tenant=tenant)
