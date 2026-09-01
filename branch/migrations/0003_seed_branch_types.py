# branch/migrations/0002_seed_branch_types.py

from django.db import migrations

DEFAULTS = ["Head Office", "Warehouse", "Branch", "Others"]

def seed_branch_types(apps, schema_editor):
    BranchType = apps.get_model("branch", "BranchType")
    for name in DEFAULTS:
        BranchType.objects.get_or_create(tenant=None, name=name)

def unseed_branch_types(apps, schema_editor):
    BranchType = apps.get_model("branch", "BranchType")
    BranchType.objects.filter(tenant__isnull=True, name__in=DEFAULTS).delete()

class Migration(migrations.Migration):
    dependencies = [("branch", "0002_branchtype")]  # confirm this matches your actual first Branch migration's name
    operations = [migrations.RunPython(seed_branch_types, unseed_branch_types)]
