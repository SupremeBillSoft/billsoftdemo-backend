# common/validation.py
#
# Shared master-data checks. Every master's code is unique per tenant at the
# database level (UniqueConstraint on tenant + code), but the tenant isn't a
# serializer field, so DRF can't see that constraint - a duplicate used to
# reach the database and come back as a 500. These turn it into a clear 400.

from django.db import IntegrityError
from django.db.models import ProtectedError
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from tenants.services import get_current_tenant


def ensure_unique_for_tenant(serializer: serializers.ModelSerializer, field: str, value, label: str):
    """Call from validate_<field>: refuse a value another record of the same
    tenant already uses (ignoring the record being edited)."""
    tenant = get_current_tenant(serializer.context.get("request"))
    clash = serializer.Meta.model.objects.filter(tenant=tenant, **{field: value})
    if serializer.instance is not None:
        clash = clash.exclude(pk=serializer.instance.pk)
    if clash.exists():
        raise serializers.ValidationError(f"{label} '{value}' already exists.")
    return value


def api_exception_handler(exc, context):
    """DRF's handler, plus a safety net: deleting a record other documents
    still reference, or any remaining database conflict (a unique constraint
    without its own check), becomes a readable 400 instead of a 500."""
    if isinstance(exc, ProtectedError):
        return Response(
            {"detail": "This record is used on other documents and can't be deleted - mark it Inactive instead."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(exc, IntegrityError):
        return Response(
            {"detail": "This record conflicts with an existing one (a duplicate code or name)."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return exception_handler(exc, context)
