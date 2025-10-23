from __future__ import annotations

from typing import Dict

from ..client import AmoCRMClient
from ..logging_utils import get_logger

LOGGER = get_logger(__name__)


def discover_pipelines(client: AmoCRMClient) -> Dict[str, Dict[int, Dict[str, object]]]:
    """Discover pipelines and statuses."""

    LOGGER.info("Discovering pipelines and statuses from amoCRM")
    pipelines: Dict[int, Dict[str, object]] = {}
    statuses: Dict[int, Dict[str, object]] = {}
    for pipeline in client.get_paginated("/api/v4/leads/pipelines"):
        pipeline_id = int(pipeline.get("id"))
        pipelines[pipeline_id] = {"name": str(pipeline.get("name") or pipeline_id)}
        embedded = pipeline.get("_embedded") if isinstance(pipeline, dict) else None
        if isinstance(embedded, dict):
            for status in embedded.get("statuses", []) or []:
                if not isinstance(status, dict):
                    continue
                status_id = status.get("id")
                if status_id is None:
                    continue
                status_id_int = int(status_id)
                statuses[status_id_int] = {
                    "name": str(status.get("name") or status_id),
                    "pipeline_id": pipeline_id,
                }
    return {"pipelines": pipelines, "statuses": statuses}


def discover_loss_reasons(client: AmoCRMClient) -> Dict[int, str]:
    LOGGER.info("Discovering loss reasons from amoCRM")
    result: Dict[int, str] = {}
    for item in client.get_paginated("/api/v4/leads/loss_reasons"):
        loss_id = item.get("id")
        if loss_id is None:
            continue
        result[int(loss_id)] = str(item.get("name") or loss_id)
    return result


def discover_users(client: AmoCRMClient) -> Dict[int, str]:
    LOGGER.info("Discovering users from amoCRM")
    result: Dict[int, str] = {}
    for item in client.get_paginated("/api/v4/users"):
        user_id = item.get("id")
        if user_id is None:
            continue
        result[int(user_id)] = str(item.get("name") or item.get("email") or user_id)
    return result


def discover_custom_fields(client: AmoCRMClient, entity: str) -> Dict[int, Dict[str, object]]:
    LOGGER.info("Discovering custom fields for entity: %s", entity)
    result: Dict[int, Dict[str, object]] = {}
    for item in client.get_paginated(f"/api/v4/{entity}/custom_fields"):
        field_id = item.get("id")
        if field_id is None:
            continue
        enums_raw = item.get("enums") if isinstance(item, dict) else None
        enums: Dict[int, str] = {}
        if isinstance(enums_raw, list):
            for enum in enums_raw:
                if not isinstance(enum, dict):
                    continue
                enum_id = enum.get("id")
                if enum_id is None:
                    continue
                enums[int(enum_id)] = str(enum.get("value") or enum_id)
        elif isinstance(enums_raw, dict):
            for enum_id, value in enums_raw.items():
                try:
                    enum_key = int(enum_id)
                except (TypeError, ValueError):
                    continue
                enums[enum_key] = str(value)
        result[int(field_id)] = {
            "name": str(item.get("name") or item.get("code") or field_id),
            "code": item.get("code"),
            "type": item.get("type"),
            "enums": enums,
        }
    return result
