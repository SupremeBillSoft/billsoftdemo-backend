from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from tenants.services import get_current_tenant

from .exports import build_workbook
from .pdf_export import build_pdf
from .models import ReportExportJob, ReportTemplate
from .registry import all_masters, get_master
from .serializers import ExportJobSerializer, ReportRequestSerializer, ReportTemplateSerializer
from .services import (
    ASYNC_EXPORT_MAX_ROWS,
    EXPORT_MAX_ROWS,
    OPERATORS,
    PDF_MAX_ROWS,
    build_queryset,
    current_user_id,
    fetch_rows,
    get_business_header,
    resolve_columns,
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class MasterReportViewSet(viewsets.ViewSet):
    """
    GET  /api/reports/masters/                       -> masters + their columns (feeds the dropdown and checklist)
    POST /api/reports/masters/<key>/run/             -> one page of rows for the chosen columns/conditions
    POST /api/reports/masters/<key>/export/          -> same query, as an .xlsx download (up to EXPORT_MAX_ROWS rows)
    POST /api/reports/masters/<key>/export-async/    -> same query as a background job, for larger reports
    POST /api/reports/masters/<key>/export-pdf/      -> same query as a PDF (also used by Print), up to PDF_MAX_ROWS rows

    No permission_classes are set here, so the project defaults apply, same as the
    master viewsets. Once Keycloak roles are wired in, filter list() by what the user
    may view and check the same permission in run/export/export-async.
    """
    lookup_field = "key"
    lookup_value_regex = r"[a-z0-9_\-]+"

    def list(self, request):
        data = [
            {
                "key": m.key,
                "label": m.label,
                "columns": [
                    {
                        "key": c.key,
                        "label": c.label,
                        "type": c.type,
                        "default": c.default,
                        "group": c.group,
                        "filterable": c.filterable,
                        "operators": list(OPERATORS[c.type]) if c.filterable else [],
                    }
                    for c in m.columns
                ],
            }
            for m in all_masters()
        ]
        return Response(data)

    # ---- helpers -------------------------------------------------------- #
    def _entry(self, key):
        entry = get_master(key)
        if entry is None:
            raise NotFound("Unknown master")
        return entry

    def _prepare(self, request, key):
        entry = self._entry(key)
        ser = ReportRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        params = ser.validated_data
        tenant = get_current_tenant(request)
        cols = resolve_columns(entry, params["columns"])
        qs, fields = build_queryset(entry, tenant, cols, params.get("conditions", []), params["logic"])
        return entry, params, tenant, cols, qs, fields

    # ---- endpoints ------------------------------------------------------ #
    @action(detail=True, methods=["post"], url_path="run")
    def run(self, request, key=None):
        entry, params, tenant, cols, qs, fields = self._prepare(request, key)

        total = qs.count()
        size = params["page_size"]
        start = (params["page"] - 1) * size
        keys = [c.key for c in cols]
        parent_once = params.get("parent_once", False)
        rows = [
            dict(zip(keys, row))
            for row in fetch_rows(entry, qs, fields, cols, parent_once, bounds=(start, start + size))
        ]

        return Response({
            "master": entry.key,
            "label": entry.label,
            "columns": [{"key": c.key, "label": c.label, "type": c.type, "group": c.group} for c in cols],
            "business": get_business_header(tenant),
            "count": total,
            "page": params["page"],
            "page_size": size,
            "sync_export_limit": EXPORT_MAX_ROWS,   # above this the screen uses export-async
            "results": rows,
        })

    @action(detail=True, methods=["post"], url_path="export")
    def export(self, request, key=None):
        entry, params, tenant, cols, qs, fields = self._prepare(request, key)

        total = qs.count()
        if total > EXPORT_MAX_ROWS:
            return Response(
                {"detail": f"{total} rows is above the direct export limit of {EXPORT_MAX_ROWS}. "
                           f"Use the background export, or add conditions to narrow the report."},
                status=400,
            )

        business = None
        if params["include_business_header"]:
            business = get_business_header(tenant)

        content = build_workbook(
            title=entry.label,
            columns=cols,
            rows=fetch_rows(entry, qs, fields, cols, params.get("parent_once", False), stream=True),
            business=business,
            include_serial=params["include_serial_no"],
        )
        filename = f"{entry.key}_report_{timezone.localtime():%Y%m%d_%H%M}.xlsx"
        response = HttpResponse(content, content_type=XLSX)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        # lets the browser frontend read the filename when CORS is in play
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response

    @action(detail=True, methods=["post"], url_path="export-pdf")
    def export_pdf(self, request, key=None):
        entry, params, tenant, cols, qs, fields = self._prepare(request, key)

        total = qs.count()
        if total > PDF_MAX_ROWS:
            return Response(
                {"detail": f"{total} rows is above the PDF limit of {PDF_MAX_ROWS}. "
                           f"Add conditions to narrow the report, or export to Excel."},
                status=400,
            )

        business = get_business_header(tenant) if params["include_business_header"] else None
        parent_once = params.get("parent_once", False)
        content = build_pdf(
            title=entry.label,
            columns=cols,
            rows=fetch_rows(entry, qs, fields, cols, parent_once, stream=True),
            business=business,
            include_serial=params["include_serial_no"],
            parent_once=parent_once,
        )
        filename = f"{entry.key}_report_{timezone.localtime():%Y%m%d_%H%M}.pdf"
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response

    @action(detail=True, methods=["post"], url_path="export-async")
    def export_async(self, request, key=None):
        entry, params, tenant, cols, qs, fields = self._prepare(request, key)

        total = qs.count()
        if total > ASYNC_EXPORT_MAX_ROWS:
            return Response(
                {"detail": f"{total} rows is above the export limit of {ASYNC_EXPORT_MAX_ROWS}. "
                           f"Add conditions to narrow the report."},
                status=400,
            )

        job = ReportExportJob.objects.create(
            tenant=tenant,
            owner=current_user_id(request),
            master_key=entry.key,
            row_count=total,
            request={
                "columns": [c.key for c in cols],
                "conditions": [dict(c) for c in params.get("conditions", [])],
                "logic": params["logic"],
                "include_business_header": params["include_business_header"],
                "include_serial_no": params["include_serial_no"],
                "parent_once": params.get("parent_once", False),
            },
        )

        if getattr(settings, "REPORTS_EXPORT_INLINE", False):
            # local development without a Celery worker: build the file right here
            from .jobs import execute_export_job
            execute_export_job(job.pk)
            job.refresh_from_db()
        else:
            from .tasks import run_export_job
            transaction.on_commit(lambda: run_export_job.delay(job.pk))

        return Response(ExportJobSerializer(job).data, status=202)


class ReportTemplateViewSet(viewsets.ModelViewSet):
    """Saved report definitions. A user sees their own templates plus the ones marked shared;
    only the owner can change or delete a template."""
    serializer_class = ReportTemplateSerializer
    pagination_class = None

    def get_queryset(self):
        tenant = get_current_tenant(self.request)
        user = current_user_id(self.request)
        qs = ReportTemplate.objects.filter(tenant=tenant).filter(Q(is_shared=True) | Q(owner=user))
        master = self.request.query_params.get("master")
        if master:
            qs = qs.filter(master_key=master)
        return qs.order_by("name")

    def perform_create(self, serializer):
        serializer.save(tenant=get_current_tenant(self.request), owner=current_user_id(self.request))

    def _require_owner(self, template):
        if template.owner != current_user_id(self.request):
            raise PermissionDenied("Only the owner of this template can change or delete it")

    def perform_update(self, serializer):
        self._require_owner(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_owner(instance)
        instance.delete()


class ReportExportJobViewSet(viewsets.ViewSet):
    """
    GET /api/reports/exports/<id>/            -> status of a background export
    GET /api/reports/exports/<id>/download/   -> the finished .xlsx file
    A job is visible only to the tenant and user that started it.
    """
    lookup_value_regex = r"\d+"

    def _job(self, request, pk):
        return get_object_or_404(
            ReportExportJob, pk=pk, tenant=get_current_tenant(request), owner=current_user_id(request)
        )

    def retrieve(self, request, pk=None):
        return Response(ExportJobSerializer(self._job(request, pk)).data)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        job = self._job(request, pk)
        if job.status != ReportExportJob.Status.DONE:
            return Response({"detail": "The export is not ready yet"}, status=409)
        if not default_storage.exists(job.file_path):
            return Response({"detail": "This export has expired. Please run it again."}, status=410)
        response = FileResponse(default_storage.open(job.file_path, "rb"), as_attachment=True,
                                filename=job.file_name, content_type=XLSX)
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response
