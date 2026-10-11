# docnumbering/models.py
#
# Port of the legacy DocNumberFormat table (see architecture doc section 11),
# tenant-scoped. A "row" here is one FORMAT VERSION, not a mutable singleton
# per document type: financial-year rollover (or any format change) closes
# the current active row (to_date set) and opens a new one with doc_no reset
# to 1, rather than resetting a counter in place. At most one row per
# (tenant, doc, doc_type, doc_sub_type) may be "active" (to_date is null) at
# a time - enforced below with a partial unique constraint.

from django.db import models

# Adjust this import to match wherever Tenant actually lives in this project
# (tenants app, per the multi-tenant pattern already used by Party/Item).
from tenants.models import Tenant


class DocumentNumberFormat(models.Model):
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="doc_number_formats"
    )

    # Three-level classification, matching the legacy Doc / Doc_Type /
    # Doc_Sub_Type columns. doc_type and doc_sub_type are blank for document
    # types that don't need variants (PO today; PR may grow the M/C/G split
    # later - the blank state already supports that with no schema change).
    doc = models.CharField(max_length=50)
    doc_type = models.CharField(max_length=50, blank=True, default="")
    doc_sub_type = models.CharField(max_length=50, blank=True, default="")

    # Optional literal-text-plus-separator blocks. All independently optional;
    # build_preview() (services.py) skips any block whose text is empty.
    prefix = models.CharField(max_length=10, blank=True, default="")
    prefix_separator = models.CharField(max_length=10, blank=True, default="")

    mid_text = models.CharField(max_length=10, blank=True, default="")
    mid_text_separator = models.CharField(max_length=10, blank=True, default="")

    suffix = models.CharField(max_length=10, blank=True, default="")
    suffix_separator = models.CharField(max_length=10, blank=True, default="")

    # The live running counter for THIS format version. Semantics: "next
    # number to issue" - starts at 1, and next_number() returns the current
    # value before incrementing it (see services.py).
    doc_no = models.PositiveIntegerField(default=1)
    # How many numbers this version has actually issued. The format (and
    # the starting Doc No) can be edited freely only while this is 0 -
    # after that, changes go through Start New Version.
    issued_count = models.PositiveIntegerField(default=0)
    format_digit = models.PositiveSmallIntegerField(default=4)  # zero-pad width
    doc_separator = models.CharField(max_length=10, blank=True, default="")

    # Financial-year label, e.g. "24-25". fin_year_1/fin_year_2 each choose
    # 2-digit or 4-digit display for that half of the FY pair; the literal
    # year values themselves come from from_date, not a stored constant, so
    # the row stays correct even if from_date is edited before activation.
    FY_DIGITS_CHOICES = [(2, "2 digits"), (4, "4 digits")]
    fin_year_1 = models.PositiveSmallIntegerField(
        choices=FY_DIGITS_CHOICES, null=True, blank=True
    )
    fin_year_2 = models.PositiveSmallIntegerField(
        choices=FY_DIGITS_CHOICES, null=True, blank=True
    )
    year_separator = models.CharField(max_length=10, blank=True, default="")

    # Where the year block sits relative to the number block.
    is_year_first = models.BooleanField(default=False)

    # Validity window for this format version. to_date null = active.
    from_date = models.DateField()
    to_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)  # legacy Chk_Box

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # At most one ACTIVE (to_date IS NULL) version per document
            # classification, per tenant. Two closed historical rows for the
            # same classification are fine and expected over time.
            models.UniqueConstraint(
                fields=["tenant", "doc", "doc_type", "doc_sub_type"],
                condition=models.Q(to_date__isnull=True),
                name="uniq_active_doc_number_format",
            )
        ]
        ordering = ["doc", "doc_type", "doc_sub_type", "-from_date"]

    def __str__(self):
        classification = "/".join(
            p for p in [self.doc, self.doc_type, self.doc_sub_type] if p
        )
        state = "active" if self.to_date is None else f"closed {self.to_date}"
        return f"{classification} ({state})"
