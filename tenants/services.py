# tenants/services.py

from django.db import transaction

from .models import DocNumberSequence, Tenant

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
