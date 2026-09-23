# parties/migrations/0004_seed_party_sub_types.py

from django.db import migrations

DEFAULT_SUB_TYPES = ["Sub Contractor", "Supplier", "Consumable Supplier", "Service Provider"]


def seed_sub_types(apps, schema_editor):
    PartySubTypeMaster = apps.get_model("parties", "PartySubTypeMaster")
    for name in DEFAULT_SUB_TYPES:
        # one-line: tenant=None — these are SYSTEM-WIDE defaults, per the
        # two-tier design already built into the model (tenant=NULL = system
        # default, a real tenant id = that tenant's own custom addition).
        PartySubTypeMaster.objects.get_or_create(tenant=None, sub_type_name=name)


def unseed_sub_types(apps, schema_editor):
    PartySubTypeMaster = apps.get_model("parties", "PartySubTypeMaster")
    PartySubTypeMaster.objects.filter(tenant__isnull=True, sub_type_name__in=DEFAULT_SUB_TYPES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("parties", "0003_add_party_bank_code"),
    ]

    operations = [
        migrations.RunPython(seed_sub_types, reverse_code=unseed_sub_types),
    ]
