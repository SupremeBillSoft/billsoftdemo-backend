# bank/views.py

from rest_framework import viewsets

from tenants.services import get_current_tenant

from .models import Bank
from .serializers import BankSerializer


class BankViewSet(viewsets.ModelViewSet):
    serializer_class = BankSerializer
    lookup_field = "bank_code"  # matches Party/Item/Unit's convention — real code in the URL, not the internal id

    def get_queryset(self):
        return Bank.objects.filter(tenant=get_current_tenant(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))
