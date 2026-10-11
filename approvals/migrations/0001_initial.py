import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("tenants", "0001_initial"),  # adjust to the actual latest tenants migration
    ]

    operations = [
        migrations.CreateModel(
            name="ApprovalHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_id", models.PositiveIntegerField()),
                ("decision", models.CharField(choices=[("Approved", "Approved"), ("Rejected", "Rejected"), ("Hold", "Hold")], max_length=20)),
                ("approved_by", models.CharField(blank=True, default="", max_length=100)),
                ("reason", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("content_type", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="contenttypes.contenttype")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="approval_history", to="tenants.tenant")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="approvalhistory",
            index=models.Index(fields=["content_type", "object_id"], name="approvals_a_content_5e4b1e_idx"),
        ),
    ]
