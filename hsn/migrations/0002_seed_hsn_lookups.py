# hsn/migrations/0002_seed_hsn_lookups.py

from django.db import migrations

ITEM_TYPES = ["Service", "Product"]
PRODUCT_CATEGORIES = ["Electronics", "Clothing", "Food", "Furniture"]

def seed(apps, schema_editor):
    HSNItemType = apps.get_model("hsn", "HSNItemType")
    HSNProductCategory = apps.get_model("hsn", "HSNProductCategory")
    for name in ITEM_TYPES:
        HSNItemType.objects.get_or_create(tenant=None, name=name)
    for name in PRODUCT_CATEGORIES:
        HSNProductCategory.objects.get_or_create(tenant=None, name=name)

def unseed(apps, schema_editor):
    HSNItemType = apps.get_model("hsn", "HSNItemType")
    HSNProductCategory = apps.get_model("hsn", "HSNProductCategory")
    HSNItemType.objects.filter(tenant__isnull=True, name__in=ITEM_TYPES).delete()
    HSNProductCategory.objects.filter(tenant__isnull=True, name__in=PRODUCT_CATEGORIES).delete()

class Migration(migrations.Migration):
    dependencies = [("hsn", "0001_initial")]  # confirm matches your actual generated filename
    operations = [migrations.RunPython(seed, unseed)]
