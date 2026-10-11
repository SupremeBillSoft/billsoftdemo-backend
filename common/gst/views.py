# common/gst/views.py
#
# Mainly for server-side revalidation of a client-computed preview - actual
# PR/PO/Invoice creation should call compute_line_tax/compute_document_totals
# directly (Python function call, not an HTTP round-trip) from within the
# same request that saves the document.

from dataclasses import asdict

from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import compute_line_tax
from .serializers import GstLineCalculationRequestSerializer


class GstLineCalculationView(APIView):
    def post(self, request):
        payload = GstLineCalculationRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = compute_line_tax(**payload.validated_data)
        return Response(asdict(result))
