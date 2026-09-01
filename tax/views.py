# tax/views.py

from rest_framework import viewsets

from tenants.services import get_current_tenant

from .models import Tax
from .serializers import TaxSerializer


class TaxViewSet(viewsets.ModelViewSet):
    serializer_class = TaxSerializer

    def get_queryset(self):
        return Tax.objects.filter(tenant=get_current_tenant(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))
