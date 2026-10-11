# docnumbering/serializers.py

from rest_framework import serializers

from .models import DocumentNumberFormat
from .services import preview


class DocumentNumberFormatSerializer(serializers.ModelSerializer):
    # Read-only, server-computed - Final Doc Format is a live preview of the
    # next number, never admin-typed. Recomputed on every read from whatever
    # the other fields currently hold.
    final_doc_format = serializers.SerializerMethodField()
    is_current_version = serializers.SerializerMethodField()
    entry_no = serializers.IntegerField(source="id", read_only=True)

    class Meta:
        model = DocumentNumberFormat
        fields = [
            "id",
            "entry_no",
            "doc",
            "doc_type",
            "doc_sub_type",
            "prefix",
            "prefix_separator",
            "doc_no",
            "issued_count",
            "format_digit",
            "doc_separator",
            "mid_text",
            "mid_text_separator",
            "fin_year_1",
            "fin_year_2",
            "year_separator",
            "suffix",
            "suffix_separator",
            "from_date",
            "to_date",
            "is_year_first",
            "is_active",
            "final_doc_format",
            "is_current_version",
        ]
        # doc_no is the starting / next number: settable until the version
        # issues its first number (enforced in the view), then only
        # next_number/start_new_version move it.
        read_only_fields = ["issued_count"]

    def validate_doc_no(self, value: int) -> int:
        if value < 1:
            raise serializers.ValidationError("Doc No must be 1 or more.")
        return value

    def validate(self, attrs):
        # One ACTIVE version per Document / Doc Type / Doc Sub Type - say so
        # instead of tripping the DB constraint with a 500.
        tenant = self.context["tenant"]
        doc = attrs.get("doc", getattr(self.instance, "doc", ""))
        doc_type = attrs.get("doc_type", getattr(self.instance, "doc_type", ""))
        doc_sub_type = attrs.get("doc_sub_type", getattr(self.instance, "doc_sub_type", ""))
        clash = DocumentNumberFormat.objects.filter(
            tenant=tenant, doc=doc, doc_type=doc_type, doc_sub_type=doc_sub_type, to_date__isnull=True
        )
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            label = " / ".join(p for p in [doc, doc_type, doc_sub_type] if p)
            raise serializers.ValidationError(
                f"'{label}' already has an active format - edit it, or use Start New Version."
            )
        return attrs

    def get_final_doc_format(self, obj: DocumentNumberFormat) -> str:
        return preview(obj, fy_start_month=self.context.get("fy_start_month"))

    def get_is_current_version(self, obj: DocumentNumberFormat) -> bool:
        return obj.to_date is None


class StartNewVersionSerializer(serializers.Serializer):
    from_date = serializers.DateField()
