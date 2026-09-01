# currency/views.py
from rest_framework import viewsets

from tenants.services import get_current_tenant

from .models import Currency
from .serializers import CurrencySerializer


class CurrencyViewSet(viewsets.ModelViewSet):
    serializer_class = CurrencySerializer
    lookup_field = "currency_code"

    def get_queryset(self):
        return Currency.objects.filter(tenant=get_current_tenant(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))
