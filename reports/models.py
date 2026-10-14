from django.db import models

from tenants.models import Tenant


class ReportTemplate(models.Model):
    """A saved report definition: which master, which columns (in order), which filters."""
    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="report_templates")

    master_key = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    columns = models.JSONField(default=list)                    # ["party_code", "party_name", ...] in display order
    conditions = models.JSONField(default=list, blank=True)     # [{"column": "city", "op": "eq", "value": "Chennai"}]
    logic = models.CharField(max_length=3, default="AND")
    show_business_header = models.BooleanField(default=True)
    show_serial_no = models.BooleanField(default=True)

    is_shared = models.BooleanField(default=False)              # visible to everyone in the tenant
    owner = models.CharField(max_length=100, blank=True)        # see services.current_user_id

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    show_parent_once = models.BooleanField(default=True)
    
    class Meta:
        db_table = "report_template"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "master_key", "owner", "name"], name="uniq_report_template_name")
        ]

    def __str__(self):
        return f"{self.master_key}: {self.name}"


class ReportExportJob(models.Model):
    """One background Excel export (used when a report is too large to build inside the request)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="report_export_jobs")
    owner = models.CharField(max_length=100, blank=True)

    master_key = models.CharField(max_length=50)
    request = models.JSONField(default=dict)                    # validated columns / conditions / logic / flags
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    row_count = models.PositiveIntegerField(null=True, blank=True)

    file_path = models.CharField(max_length=500, blank=True)    # path inside the default storage (MinIO / media)
    file_name = models.CharField(max_length=255, blank=True)    # name offered to the browser
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "report_export_job"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.master_key} export #{self.pk} ({self.status})"
