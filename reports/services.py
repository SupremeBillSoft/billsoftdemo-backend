"""Query building for the generic master report. No raw SQL anywhere: every
column and operator comes from the registry / the fixed tables below."""
import operator
from functools import reduce

from django.apps import apps
from django.db.models import Q
from rest_framework import serializers

from .registry import BOOL, DATE, DATETIME, NUMBER, TEXT

MAX_PAGE_SIZE = 200
MAX_CONDITIONS = 20
PDF_MAX_ROWS = 5000              # PDFs are built in memory and are heavy to read; larger reports use Excel
EXPORT_MAX_ROWS = 20000          # above this, the export runs as a background job
ASYNC_EXPORT_MAX_ROWS = 500000   # hard ceiling for background exports (Excel itself stops at ~1,048,000)

# (app_label, model_name) of the company settings model (one row per tenant).
# CompanySettings is defined in the tenants app, so the app label is "tenants".
BUSINESS_INFO_MODEL = ("tenants", "CompanySettings")

_COMPARE = ("eq", "ne", "gt", "gte", "lt", "lte")
OPERATORS = {
    TEXT: ("eq", "ne", "contains", "startswith"),
    NUMBER: _COMPARE,
    BOOL: ("eq",),
    DATE: _COMPARE,
    DATETIME: _COMPARE,   # compared by calendar date
}
_LOOKUPS = {
    "eq": "exact", "ne": "exact", "gt": "gt", "gte": "gte", "lt": "lt", "lte": "lte",
    "contains": "icontains", "startswith": "istartswith",
}
_COERCERS = {
    TEXT: serializers.CharField(),
    NUMBER: serializers.FloatField(),
    BOOL: serializers.BooleanField(),
    DATE: serializers.DateField(),
    DATETIME: serializers.DateField(),
}


def _err(message):
    return serializers.ValidationError({"conditions": [message]})


def resolve_columns(entry, keys):
    cols, seen = [], set()
    for key in keys:
        if key in seen:
            continue
        col = entry.get_column(key)
        if col is None:
            raise serializers.ValidationError({"columns": [f"Unknown column '{key}' for {entry.label}"]})
        seen.add(key)
        cols.append(col)
    return cols


def _condition_q(entry, cond, position):
    col = entry.get_column(cond["column"])
    if col is None or not col.filterable:
        raise _err(f"Condition {position}: column '{cond['column']}' cannot be filtered")
    op = cond["op"]
    if op not in OPERATORS[col.type]:
        raise _err(f"Condition {position}: operator '{op}' is not valid for {col.label}")
    try:
        value = _COERCERS[col.type].run_validation(cond.get("value"))
    except serializers.ValidationError:
        raise _err(f"Condition {position}: invalid value for {col.label}")

    lookup = col.lookup + ("__date" if col.type == DATETIME else "")
    name = "iexact" if (col.type == TEXT and op in ("eq", "ne")) else _LOOKUPS[op]
    q = Q(**{f"{lookup}__{name}": value})
    return ~q if op == "ne" else q


def build_filter(entry, conditions, logic):
    if len(conditions) > MAX_CONDITIONS:
        raise _err(f"At most {MAX_CONDITIONS} conditions are allowed")
    parts = [_condition_q(entry, c, i + 1) for i, c in enumerate(conditions)]
    if not parts:
        return Q()
    return reduce(operator.and_ if logic == "AND" else operator.or_, parts)


def build_queryset(entry, tenant, cols, conditions, logic):
    """Returns (queryset, field_names) where field_names line up with cols."""
    qs = entry.scope(tenant)
    q = build_filter(entry, conditions, logic)
    if q:
        qs = qs.filter(q)

    fields = []
    for col in cols:
        if col.annotation:
            # alias prefix avoids clashing with real model field names (e.g. M2M "sub_types")
            alias = f"_ann_{col.key}"
            qs = qs.annotate(**{alias: col.annotation()})
            fields.append(alias)
        else:
            fields.append(col.lookup)
    return qs.order_by(*(entry.ordering or ("pk",))), fields


def fetch_rows(entry, qs, fields, cols, parent_once=False, bounds=None, stream=False):
    """Row tuples lined up with `cols`: one page (bounds=(start, stop)), a streamed
    iterator for exports (stream=True), or the plain queryset.

    parent_once (child reports only): the parent's columns are filled on the first
    row of each parent and left empty (None) on its following rows. Rows are already
    ordered parent-first, so this is one pass. On a paged grid the first row of every
    page shows the parent again, so a page never starts with empty parent cells."""
    parent_idx = [i for i, c in enumerate(cols) if c.group]
    blank = bool(parent_once and entry.parent_fk and parent_idx)

    rows = qs.values_list(*fields, entry.parent_fk) if blank else qs.values_list(*fields)
    if bounds is not None:
        rows = rows[bounds[0]:bounds[1]]
    elif stream:
        rows = rows.iterator(chunk_size=2000)
    return _blank_repeats(rows, parent_idx) if blank else rows


def _blank_repeats(rows, parent_idx):
    previous = object()
    for *values, parent_id in rows:
        if parent_id == previous:
            for i in parent_idx:
                values[i] = None
        previous = parent_id
        yield tuple(values)


def get_business_header(tenant):
    """Header block for grid and Excel, from the tenant's Company Settings row.
    Read-only: it never creates the row. Returns None until a business name has been saved."""
    try:
        Settings = apps.get_model(*BUSINESS_INFO_MODEL)
    except LookupError:
        return None
    c = Settings.objects.filter(tenant=tenant).first()
    if c is None or not c.business_name:
        return None

    state_pin = " - ".join(p for p in (c.state, c.pincode) if p)
    address = ", ".join(p for p in (c.address_1, c.address_2, c.city, state_pin, c.country) if p)
    contact = " | ".join(
        p for p in (
            f"GSTIN: {c.gstin}" if c.gstin else "",
            f"Phone: {c.phone}" if c.phone else "",
            f"Email: {c.email}" if c.email else "",
        ) if p
    )
    return {"name": c.business_name, "address": address, "contact": contact}


def current_user_id(request) -> str:
    """Who is asking. The ONE place to change once Keycloak authentication is wired
    (for example return the token's 'sub' or 'preferred_username'). Used to own
    saved templates and export jobs."""
    return (getattr(request.user, "username", "") or "")[:100]
