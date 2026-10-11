# tenants/views.py

from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import CompanySettingsSerializer
from .services import get_company_settings, get_current_tenant


class CompanySettingsView(APIView):
    """GET/PATCH /api/company-settings/ - the current tenant's single
    Company Settings record (created on first read)."""

    def get(self, request):
        settings = get_company_settings(get_current_tenant(request))
        return Response(CompanySettingsSerializer(settings).data)

    def patch(self, request):
        settings = get_company_settings(get_current_tenant(request))
        serializer = CompanySettingsSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
