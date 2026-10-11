# docnumbering/tasks.py
#
# Automatic FY rollover, per your confirmation: build it automatic, with
# manual override always available via the settings screen's Start New
# Version action (services.start_new_version - the same function this task
# calls, so the two paths can never drift apart).
#
# Reads the financial-year start month from Company Settings (default 4,
# April). NOTE: Celery isn't installed/deployed yet - until it is, use the
# settings screen's Start New Version at each year end.

from __future__ import annotations

import datetime

from celery import shared_task
from django.utils import timezone

from tenants.models import Tenant
from tenants.services import get_company_settings

from .models import DocumentNumberFormat
from .services import start_new_version


def _current_fy_start(today: datetime.date, fy_start_month: int) -> datetime.date:
    fy_start_year = today.year if today.month >= fy_start_month else today.year - 1
    return today.replace(year=fy_start_year, month=fy_start_month, day=1)


@shared_task
def rollover_financial_year_formats() -> None:
    """Scheduled daily via Celery beat. For every tenant, finds any active
    DocumentNumberFormat row whose from_date predates the tenant's CURRENT
    financial-year start, and rolls it into a fresh version dated at that
    FY start (doc_no reset to 1). A row created partway through the current
    FY is left alone - only rows still running on a PRIOR FY get rolled."""
    today = timezone.localdate()

    for tenant in Tenant.objects.all():
        fy_start_month = get_company_settings(tenant).financial_year_start_month
        fy_start = _current_fy_start(today, fy_start_month)

        stale_active_formats = DocumentNumberFormat.objects.filter(
            tenant=tenant, to_date__isnull=True, from_date__lt=fy_start
        )
        for fmt in stale_active_formats:
            start_new_version(fmt, from_date=fy_start)
