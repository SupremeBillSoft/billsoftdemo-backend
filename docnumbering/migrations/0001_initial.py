import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        # Adjust to the actual latest tenants migration in this project.
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentNumberFormat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("doc", models.CharField(max_length=50)),
                ("doc_type", models.CharField(blank=True, default="", max_length=50)),
                ("doc_sub_type", models.CharField(blank=True, default="", max_length=50)),
                ("prefix", models.CharField(blank=True, default="", max_length=10)),
                ("prefix_separator", models.CharField(blank=True, default="", max_length=10)),
                ("mid_text", models.CharField(blank=True, default="", max_length=10)),
                ("mid_text_separator", models.CharField(blank=True, default="", max_length=10)),
                ("suffix", models.CharField(blank=True, default="", max_length=10)),
                ("suffix_separator", models.CharField(blank=True, default="", max_length=10)),
                ("doc_no", models.PositiveIntegerField(default=1)),
                ("format_digit", models.PositiveSmallIntegerField(default=4)),
                ("doc_separator", models.CharField(blank=True, default="", max_length=10)),
                ("fin_year_1", models.PositiveSmallIntegerField(blank=True, choices=[(2, "2 digits"), (4, "4 digits")], null=True)),
                ("fin_year_2", models.PositiveSmallIntegerField(blank=True, choices=[(2, "2 digits"), (4, "4 digits")], null=True)),
                ("year_separator", models.CharField(blank=True, default="", max_length=10)),
                ("is_year_first", models.BooleanField(default=False)),
                ("from_date", models.DateField()),
                ("to_date", models.DateField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="doc_number_formats", to="tenants.tenant")),
            ],
            options={
                "ordering": ["doc", "doc_type", "doc_sub_type", "-from_date"],
            },
        ),
        migrations.AddConstraint(
            model_name="documentnumberformat",
            constraint=models.UniqueConstraint(
                condition=models.Q(("to_date__isnull", True)),
                fields=("tenant", "doc", "doc_type", "doc_sub_type"),
                name="uniq_active_doc_number_format",
            ),
        ),
    ]
