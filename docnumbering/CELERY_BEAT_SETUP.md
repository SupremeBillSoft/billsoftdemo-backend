# Wiring the FY rollover task

## 1. Add to your Celery beat schedule (wherever the project's other periodic
tasks are configured, e.g. settings.py's `CELERY_BEAT_SCHEDULE`):

```python
CELERY_BEAT_SCHEDULE = {
    # ...existing entries...
    "docnumbering-fy-rollover": {
        "task": "docnumbering.tasks.rollover_financial_year_formats",
        "schedule": crontab(hour=0, minute=30),  # once daily, just after midnight
    },
}
```

Running it daily (not just once a year) is deliberate: it makes the check
idempotent and self-healing - if a tenant is created mid-year, or the task
fails one day, the very next run still catches any format that's fallen
behind the tenant's current financial year, with no special-casing needed.

## 2. Add `financial_year_start_month` to the tenant/company-settings model

This is a genuinely new field - the Financial Year master itself is deferred
(architecture doc section 8), but the rollover task needs *something* to
compare against. Wherever your Business_Information / tenant settings model
lives (this may be a separate app - e.g. `currency` - rather than
`docnumbering`, in which case its migration is generated and run there,
alongside `docnumbering`'s, not instead of it):

```python
financial_year_start_month = models.PositiveSmallIntegerField(
    default=4,  # April, matching GST financial-year convention.
    help_text="Month (1-12) this tenant's financial year begins.",
)
```

Then:

```bash
python manage.py makemigrations <app_that_owns_this_model>
python manage.py migrate
```

Every tenant gets the same default (April) unless a specific tenant's
Company Settings is edited to say otherwise - no region-specific logic
needed, since the field is just a plain per-tenant setting.

## 3. Seeding new tenants

Call `docnumbering.services.seed_default_formats_for_tenant(tenant, today)`
from wherever the Supreme Softs admin portal provisions a new tenant, so PR/
PO/etc. have a working number format from day one.
