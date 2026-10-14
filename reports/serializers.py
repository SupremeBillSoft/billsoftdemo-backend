from rest_framework import serializers

from tenants.services import get_current_tenant

from .models import ReportExportJob, ReportTemplate
from .registry import get_master
from .services import MAX_PAGE_SIZE, build_filter, current_user_id, resolve_columns


class ConditionSerializer(serializers.Serializer):
    column = serializers.CharField(max_length=64)
    op = serializers.CharField(max_length=16)
    value = serializers.JSONField(required=False, allow_null=True)


class ReportRequestSerializer(serializers.Serializer):
    """Body for both /run/ and /export/. Column keys and operators are checked
    against the registry in services.py, so nothing arbitrary reaches the ORM."""
    columns = serializers.ListField(child=serializers.CharField(max_length=64), allow_empty=False, max_length=60)
    conditions = ConditionSerializer(many=True, required=False)
    logic = serializers.ChoiceField(choices=["AND", "OR"], default="AND")

    # /run/ only
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=MAX_PAGE_SIZE, default=50)

    # /export/ only
    include_business_header = serializers.BooleanField(default=True)
    include_serial_no = serializers.BooleanField(default=True)
    parent_once = serializers.BooleanField(required=False, default=False)   # child reports only



    
class ReportTemplateSerializer(serializers.ModelSerializer):
    conditions = ConditionSerializer(many=True, required=False)

    class Meta:
        model = ReportTemplate
        fields = [
            "id", "master_key", "name", "columns", "conditions", "logic",
            "show_business_header", "show_serial_no", "is_shared", "owner",
            "created_at", "updated_at", "show_parent_once"                        
        ]
        read_only_fields = ["id", "owner", "created_at", "updated_at"]

    def validate_columns(self, value):
        if not value or not isinstance(value, list) or not all(isinstance(k, str) for k in value):
            raise serializers.ValidationError("Choose at least one column")
        return list(dict.fromkeys(value))          # drop duplicates, keep order

    def validate_logic(self, value):
        if value not in ("AND", "OR"):
            raise serializers.ValidationError("Logic must be AND or OR")
        return value

    def validate(self, data):
        inst = self.instance
        master_key = data.get("master_key", inst.master_key if inst else None)
        entry = get_master(master_key)
        if entry is None:
            raise serializers.ValidationError({"master_key": "Unknown master"})

        columns = data.get("columns", inst.columns if inst else [])
        conditions = [dict(c) for c in data.get("conditions", inst.conditions if inst else [])]
        logic = data.get("logic", inst.logic if inst else "AND")
        # same checks the report itself applies, so a template can never hold something /run/ would reject
        resolve_columns(entry, columns)
        build_filter(entry, conditions, logic)
        data["conditions"] = conditions

        # tenant and owner are not serializer fields, so DRF cannot see the unique constraint itself
        request = self.context["request"]
        name = data.get("name", inst.name if inst else "")
        clash = ReportTemplate.objects.filter(
            tenant=get_current_tenant(request), master_key=master_key, owner=current_user_id(request), name=name
        )
        if inst:
            clash = clash.exclude(pk=inst.pk)
        if clash.exists():
            raise serializers.ValidationError({"name": "You already have a template with this name for this master"})
        return data

    def update(self, instance, validated_data):
        validated_data.pop("master_key", None)     # a template stays with its master
        return super().update(instance, validated_data)


class ExportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportExportJob
        fields = ["id", "master_key", "status", "row_count", "file_name", "error",
                  "created_at", "started_at", "finished_at"]
        read_only_fields = fields
