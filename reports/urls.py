from rest_framework.routers import DefaultRouter

from .views import MasterReportViewSet, ReportExportJobViewSet, ReportTemplateViewSet

router = DefaultRouter()
router.register("reports/masters", MasterReportViewSet, basename="master-report")
router.register("reports/templates", ReportTemplateViewSet, basename="report-template")
router.register("reports/exports", ReportExportJobViewSet, basename="report-export")

urlpatterns = router.urls
