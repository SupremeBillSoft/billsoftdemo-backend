# tenants/services.py

from django.db import transaction

from .models import CompanySettings, DocNumberSequence, Tenant

# one-line: TEMPORARY — every viewset calls THIS function to get the
# current tenant, never Tenant.objects.get(...) scattered across files.
# Centralizing it here means: when Keycloak/JWT auth is wired in, this is
# the ONLY function that changes (reads request.user.profile.tenant instead
# of returning a hardcoded one) — every viewset that calls it is instantly
# correct with zero further edits.
DEV_TENANT_SUBDOMAIN = "dev-tenant"

def get_current_tenant(request):
    # TODO: replace with request.user.profile.tenant once Keycloak/JWT auth exists.
    tenant, _ = Tenant.objects.get_or_create(
        subdomain=DEV_TENANT_SUBDOMAIN,
        defaults={"tenant_name": "Dev Tenant (temporary, hardcoded)"},
    )
    return tenant

def get_company_settings(tenant) -> CompanySettings:
    settings, _ = CompanySettings.objects.get_or_create(tenant=tenant)
    return settings


# one-line: TEMPORARY - the single place "who did this" is resolved, used for
# Created By/Updated By on stock movements, adjustments and transfers. There
# is no login yet, so every request is AnonymousUser. Once registration and
# login exist, return the logged-in user's name here (same deal as
# get_current_tenant above) and every caller is correct with no further edits.
def current_user_name(request) -> str:
    # TODO: replace with request.user's name once login is wired in.
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user.get_full_name() or user.username
    return "Unknown (login pending)"


# TEMPORARY - the logged-in user's role (BRD User Management: Super Admin /
# Admin / Manager / User), used for role-based PR/PO auto-approval. No login
# yet, so there is no role and nothing is auto-approved. Return the user's
# role here once login exists.
def current_user_role(request) -> str | None:
    # TODO: replace with request.user's role once login is wired in.
    return None


@transaction.atomic
def get_next_sequence_number(tenant, doc_type: str, digits: int) -> str:
    # one-line: select_for_update() locks this row for the duration of the
    # transaction — this IS the fix for the concurrency risk we flagged
    # when the frontend had a client-side counter. Two simultaneous
    # requests can no longer both read "5" and both save as "0005".
    seq, _ = DocNumberSequence.objects.select_for_update().get_or_create(
        tenant=tenant, doc_type=doc_type, defaults={"next_number": 1}
    )
    number = seq.next_number
    seq.next_number += 1
    seq.save()
    return str(number).zfill(digits)
