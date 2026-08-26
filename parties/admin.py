# parties/admin.py
from django.contrib import admin

from .models import Party, PartyBankDetail, PartyContact, PartyItem, PartyShippingAddress, PartySubTypeMaster


class PartyContactInline(admin.TabularInline):
    model = PartyContact
    extra = 0

class PartyItemInline(admin.TabularInline):
    model = PartyItem
    extra = 0

class PartyBankDetailInline(admin.TabularInline):
    model = PartyBankDetail
    extra = 0

class PartyShippingAddressInline(admin.TabularInline):
    model = PartyShippingAddress
    extra = 0

@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ["party_code", "party_name", "tenant", "status"]
    inlines = [PartyContactInline, PartyItemInline, PartyBankDetailInline, PartyShippingAddressInline]

admin.site.register(PartySubTypeMaster)
