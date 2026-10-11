# tax/views.py

from django.db.models import ProtectedError
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from tenants.services import get_current_tenant

from .models import Tax
from .serializers import TaxSerializer


class TaxViewSet(viewsets.ModelViewSet):
    serializer_class = TaxSerializer

    def get_queryset(self):
        return Tax.objects.filter(tenant=get_current_tenant(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request))

    def perform_destroy(self, instance):
        # Purchase document lines keep the Tax they were taxed with.
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError("This tax is used on saved documents - mark it Inactive instead.") from exc
