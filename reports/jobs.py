"""Background export logic. Plain Python on purpose (no Celery import), so it can run
inside a Celery task, or inline for local development with REPORTS_EXPORT_INLINE = True."""
import logging
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from .exports import build_workbook
from .models import ReportExportJob
from .registry import get_master
from .services import build_queryset,fetch_rows, get_business_header, resolve_columns



rows=fetch_rows(entry, qs, fields, cols, spec.get("parent_once", False), stream=True),

logger = logging.getLogger(__name__)

EXPORT_FOLDER = "report-exports"
KEEP_DAYS = 7


def execute_export_job(job_id):
    job = ReportExportJob.objects.select_related("tenant").filter(pk=job_id).first()
    # Only a pending job runs: a repeated delivery of the same Celery message must not build the file twice.
    if job is None or job.status != ReportExportJob.Status.PENDING:
        return

    job.status = ReportExportJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])

    try:
        entry = get_master(job.master_key)
        if entry is None:
            raise ValueError(f"Unknown master '{job.master_key}'")
        spec = job.request
        cols = resolve_columns(entry, spec["columns"])
        # Same tenant-scoped query as the screen: entry.scope(tenant) is applied inside build_queryset.
        # When RLS is added, set the tenant context for this connection before this line.
        qs, fields = build_queryset(entry, job.tenant, cols, spec.get("conditions", []), spec.get("logic", "AND"))
        business = get_business_header(job.tenant) if spec.get("include_business_header", True) else None

        content = build_workbook(
            title=entry.label,
            columns=cols,
            rows=qs.values_list(*fields).iterator(chunk_size=2000),
            business=business,
            include_serial=spec.get("include_serial_no", True),
            streaming=True,
        )
        file_name = f"{entry.key}_report_{timezone.localtime():%Y%m%d_%H%M}.xlsx"
        job.file_path = default_storage.save(f"{EXPORT_FOLDER}/{job.tenant_id}/{job.pk}_{file_name}", ContentFile(content))
        job.file_name = file_name
        job.status = ReportExportJob.Status.DONE
    except Exception as exc:  # the job row carries the failure to the user; the log carries the traceback
        logger.exception("Report export job %s failed", job_id)
        job.status = ReportExportJob.Status.FAILED
        job.error = str(exc)[:500]
    job.finished_at = timezone.now()
    job.save()


def purge_old_export_jobs(days=KEEP_DAYS):
    """Delete finished export files and their job rows older than `days`. Returns how many were removed."""
    cutoff = timezone.now() - timedelta(days=days)
    removed = 0
    for job in ReportExportJob.objects.filter(created_at__lt=cutoff):
        if job.file_path:
            try:
                default_storage.delete(job.file_path)
            except Exception:
                logger.warning("Could not delete export file %s", job.file_path, exc_info=True)
        job.delete()
        removed += 1
    return removed
