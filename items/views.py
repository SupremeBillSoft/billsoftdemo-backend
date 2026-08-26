# items/views.py

from rest_framework import viewsets

from tenants.services import get_current_tenant

from .models import Item
from .serializers import ItemSerializer


class ItemViewSet(viewsets.ModelViewSet):
    serializer_class = ItemSerializer
    lookup_field = "item_code"

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        return Item.objects.filter(tenant=tenant).prefetch_related("custom_fields")

    def perform_create(self, serializer):
        tenant = get_current_tenant(self.request)
        serializer.save(tenant=tenant)
