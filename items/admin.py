# items/admin.py
from django.contrib import admin

from .models import Item, ItemCustomField


class ItemCustomFieldInline(admin.TabularInline):
    model = ItemCustomField
    extra = 0

@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["item_code", "item_name", "tenant", "item_status"]
    inlines = [ItemCustomFieldInline]

