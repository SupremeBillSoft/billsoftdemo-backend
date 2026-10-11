# Company State/GSTIN used to be read from each tenant's first Branch (a
# placeholder). Now that Company Settings owns them (BRD 6.7.1), carry the
# old values over once so GST calculations don't change for existing data.

from django.db import migrations


def seed_from_first_branch(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    CompanySettings = apps.get_model("tenants", "CompanySettings")
    Branch = apps.get_model("branch", "Branch")
    for tenant in Tenant.objects.all():
        settings, _ = CompanySettings.objects.get_or_create(tenant=tenant)
        branch = Branch.objects.filter(tenant=tenant).order_by("id").first()
        if branch is None or settings.state:
            continue
        settings.state = branch.state
        settings.city = settings.city or branch.city
        settings.pincode = settings.pincode or branch.pincode
        settings.business_name = settings.business_name or tenant.tenant_name
        if branch.gstin and len(branch.gstin) == 15:
            settings.gstin = branch.gstin.upper()
            settings.gst_registered = True
        settings.save()


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0003_companysettings'),
        ('branch', '0004_rename_address_line1_branch_address_line_1_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_from_first_branch, migrations.RunPython.noop),
    ]
