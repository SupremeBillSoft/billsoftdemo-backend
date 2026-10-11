# docnumbering/views.py

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

# Confirmed: tenants/services.py, and it takes the request (not a
# thread-local no-arg lookup, as I'd first guessed).
from tenants.services import get_company_settings, get_current_tenant

from .models import DocumentNumberFormat
from .serializers import DocumentNumberFormatSerializer, StartNewVersionSerializer
from .services import start_new_version


class DocumentNumberFormatViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentNumberFormatSerializer

    def get_queryset(self):
        qs = DocumentNumberFormat.objects.filter(tenant=get_current_tenant(self.request))
        doc = self.request.query_params.get("doc")
        if doc:
            qs = qs.filter(doc=doc)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        tenant = get_current_tenant(self.request)
        context["tenant"] = tenant
        context["fy_start_month"] = get_company_settings(tenant).financial_year_start_month
        return context

    def perform_create(self, serializer):
        # A brand-new classification (no prior version at all) is the only
        # case a plain create is allowed for; changing an EXISTING active
        # classification's format goes through start_new_version below, not
        # a create or an in-place edit of the active row's issuing fields.
        serializer.save(tenant=get_current_tenant(self.request), to_date=None)

    def perform_update(self, serializer):
        # Free-edit in place ONLY while nothing has been issued yet under
        # this version. Once a number has actually gone out, changing the
        # format has to go through start_new_version so already-issued
        # numbers stay under their original format.
        instance: DocumentNumberFormat = self.get_object()
        if instance.issued_count > 0:
            raise ValidationError(
                "This format has already issued numbers - use Start New Version "
                "to change it going forward instead of editing it in place."
            )
        serializer.save()

    @action(detail=True, methods=["post"], url_path="start-new-version")
    def start_new_version_action(self, request, pk=None):
        """Manual equivalent of the FY-rollover task: closes this row and
        opens a fresh one (doc_no reset to 1) carrying forward the format.
        Use this to change a format going forward without disturbing
        numbers already issued under the old one."""
        current = self.get_object()
        if current.to_date is not None:
            return Response(
                {"detail": "This format version is already closed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = StartNewVersionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        new_fmt = start_new_version(current, from_date=payload.validated_data["from_date"])
        return Response(
            DocumentNumberFormatSerializer(new_fmt, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_destroy(self, instance):
        # Legacy list screen has Delete; a version that has issued numbers
        # is history and stays.
        if instance.issued_count > 0:
            raise ValidationError("This format has already issued numbers and cannot be deleted.")
        instance.delete()
