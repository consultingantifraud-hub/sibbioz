from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..logging_utils import get_logger
from .cache import MetadataBundle

LOGGER = get_logger(__name__)


@dataclass
class CustomFieldSchema:
    field_id: int
    name: str
    code: Optional[str]
    field_type: str
    enums: Dict[int, str]
    column_key: str


class MetadataMapping:
    def __init__(self, bundle: MetadataBundle) -> None:
        self._bundle = bundle
        self._status_map: Dict[int, Dict[str, str]] = {}
        for status_id, status in bundle.statuses.items():
            pipeline_id = int(status.get("pipeline_id")) if status.get("pipeline_id") is not None else None
            pipeline_name = None
            if pipeline_id is not None:
                pipeline = bundle.pipelines.get(int(pipeline_id), {})
                pipeline_name = str(pipeline.get("name")) if pipeline else None
            self._status_map[int(status_id)] = {
                "status_name": str(status.get("name")) if status.get("name") is not None else None,
                "pipeline_name": pipeline_name,
            }
        self._loss_reasons = {int(k): v for k, v in bundle.loss_reasons.items()}
        self._users = {int(k): v for k, v in bundle.users.items()}
        self._fields: Dict[str, Dict[int, CustomFieldSchema]] = {
            entity: self._prepare_fields(entity, fields)
            for entity, fields in bundle.custom_fields.items()
        }
        self._unknown_field_types: set[str] = set()

    def _prepare_fields(self, entity: str, fields: Dict[int, Dict[str, object]]) -> Dict[int, CustomFieldSchema]:
        prepared: Dict[int, CustomFieldSchema] = {}
        for field_id, info in fields.items():
            name = str(info.get("name") or field_id)
            code = info.get("code")
            field_type = str(info.get("type") or "unknown").lower()
            column_key = self._make_column_key(field_id, name=name, code=code)
            enums = {int(k): str(v) for k, v in (info.get("enums") or {}).items()}
            prepared[int(field_id)] = CustomFieldSchema(
                field_id=int(field_id),
                name=name,
                code=str(code) if code else None,
                field_type=field_type,
                enums=enums,
                column_key=column_key,
            )
        return prepared

    @staticmethod
    def _make_column_key(field_id: int, *, name: str, code: Optional[str]) -> str:
        if code:
            base = f"cf_{str(code).lower()}"
        else:
            slug = slugify(name)
            if not slug:
                slug = "field"
            base = f"cf_{slug}"
        return f"{base}__f{field_id}"

    def map_status(self, status_id: Optional[int]) -> Dict[str, Optional[str]]:
        if status_id is None:
            return {"status_name": None, "pipeline_name": None}
        return self._status_map.get(int(status_id), {"status_name": None, "pipeline_name": None})

    def map_loss_reason(self, loss_reason_id: Optional[int]) -> Optional[str]:
        if loss_reason_id is None:
            return None
        return self._loss_reasons.get(int(loss_reason_id))

    def map_user(self, user_id: Optional[int]) -> Optional[str]:
        if user_id is None:
            return None
        return self._users.get(int(user_id))

    def flatten_custom_fields(self, entity: str, custom_fields_values: Optional[List[Dict[str, object]]]) -> Dict[str, object]:
        schema_map = self._fields.get(entity, {})
        result: Dict[str, object] = {}
        if not custom_fields_values:
            return result
        for field in custom_fields_values:
            if not isinstance(field, dict):
                continue
            field_id_raw = field.get("field_id")
            if field_id_raw is None:
                continue
            try:
                field_id = int(field_id_raw)
            except (TypeError, ValueError):
                continue
            schema = schema_map.get(field_id)
            values = field.get("values")
            if schema is None:
                column_name = f"cf_unknown__f{field_id}"
                result[column_name] = json.dumps(values, ensure_ascii=False)
                continue
            column_name = schema.column_key
            parsed = self._extract_values(schema, values)
            for key, value in parsed.items():
                result[key] = value
        return result

    def _extract_values(self, schema: CustomFieldSchema, values: object) -> Dict[str, object]:
        extractor = getattr(self, f"_extract_{schema.field_type}", None)
        if extractor is None:
            if schema.field_type not in self._unknown_field_types:
                LOGGER.warning("Unknown custom field type '%s' for field %s", schema.field_type, schema.field_id)
                self._unknown_field_types.add(schema.field_type)
            return {f"cf_raw__f{schema.field_id}": json.dumps(values, ensure_ascii=False)}
        return extractor(schema, values)  # type: ignore[misc]

    def _extract_text(self, schema: CustomFieldSchema, values: object) -> Dict[str, object]:
        return {schema.column_key: self._join_values(values)}

    _extract_textarea = _extract_url = _extract_numeric = _extract_price = _extract_monetary = _extract_text

    def _extract_checkbox(self, schema: CustomFieldSchema, values: object) -> Dict[str, object]:
        raw = self._first_value(values)
        try:
            as_int = int(raw)
        except (TypeError, ValueError):
            as_int = 0
        return {schema.column_key: as_int}

    def _extract_date(self, schema: CustomFieldSchema, values: object) -> Dict[str, object]:
        raw = self._first_value(values)
        iso = ensure_iso_datetime(raw)
        return {schema.column_key: raw, f"{schema.column_key}_dt": iso}

    _extract_date_time = _extract_date

    def _extract_select(self, schema: CustomFieldSchema, values: object) -> Dict[str, object]:
        raw = self._first_dict(values)
        enum_id = raw.get("enum_id") if isinstance(raw, dict) else None
        enum_value = None
        if enum_id is not None:
            try:
                enum_value = schema.enums.get(int(enum_id))
            except (TypeError, ValueError):
                enum_value = None
        if enum_value is None:
            enum_value = raw.get("value") if isinstance(raw, dict) else None
        return {
            schema.column_key: enum_value,
            f"{schema.column_key}_id": enum_id,
        }

    def _extract_multiselect(self, schema: CustomFieldSchema, values: object) -> Dict[str, object]:
        items: List[str] = []
        ids: List[str] = []
        if isinstance(values, list):
            for value in values:
                if not isinstance(value, dict):
                    continue
                enum_id = value.get("enum_id")
                if enum_id is None:
                    continue
                ids.append(str(enum_id))
                label = None
                try:
                    label = schema.enums.get(int(enum_id))
                except (TypeError, ValueError):
                    label = None
                if not label:
                    label = str(value.get("value")) if value.get("value") is not None else ""
                items.append(label)
        return {
            schema.column_key: "; ".join(filter(None, items)) if items else None,
            f"{schema.column_key}_ids": "; ".join(ids) if ids else None,
        }

    def _extract_multitext(self, schema: CustomFieldSchema, values: object) -> Dict[str, object]:
        items = self._collect_values(values)
        return {
            schema.column_key: "; ".join(items) if items else None,
            f"{schema.column_key}_count": len(items),
        }

    @staticmethod
    def _first_value(values: object) -> Optional[object]:
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, dict):
                return first.get("value")
            return first
        return None

    @staticmethod
    def _collect_values(values: object) -> List[str]:
        collected: List[str] = []
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    value = item.get("value")
                else:
                    value = item
                if value is None:
                    continue
                collected.append(str(value))
        return collected

    @staticmethod
    def _first_dict(values: object) -> Dict[str, object]:
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    return item
        return {}

    @staticmethod
    def _join_values(values: object) -> Optional[str]:
        items = MetadataMapping._collect_values(values)
        if not items:
            return None
        return "; ".join(items)


def ensure_iso_datetime(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _to_iso_from_timestamp(float(value))
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.isdigit():
            return _to_iso_from_timestamp(float(value))
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.isoformat()
            except ValueError:
                continue
        return value
    return None


def _to_iso_from_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


_slug_re = re.compile(r"[^a-z0-9]+", re.IGNORECASE)


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = _slug_re.sub("_", normalized)
    normalized = normalized.strip("_")
    return normalized


_MAPPER: Optional[MetadataMapping] = None


def configure_mapping(bundle: MetadataBundle) -> None:
    global _MAPPER
    _MAPPER = MetadataMapping(bundle)
    LOGGER.info("Metadata mapping configured")


def _ensure_mapper() -> MetadataMapping:
    if _MAPPER is None:
        raise RuntimeError("Metadata mapping has not been configured")
    return _MAPPER


def map_status(status_id: Optional[int]) -> Dict[str, Optional[str]]:
    return _ensure_mapper().map_status(status_id)


def map_loss_reason(loss_reason_id: Optional[int]) -> Optional[str]:
    return _ensure_mapper().map_loss_reason(loss_reason_id)


def map_user(user_id: Optional[int]) -> Optional[str]:
    return _ensure_mapper().map_user(user_id)


def flatten_custom_fields(entity: str, custom_fields_values: Optional[List[Dict[str, object]]]) -> Dict[str, object]:
    return _ensure_mapper().flatten_custom_fields(entity, custom_fields_values)
