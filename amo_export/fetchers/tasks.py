from __future__ import annotations

from typing import Dict, List, Optional

from ..client import AmoCRMClient
from ..meta import map_user
from .base import enrich_datetime_columns, fetch_entities, to_dataframe


def fetch_tasks(
    client: AmoCRMClient,
    *,
    updated_from: Optional[int] = None,
    updated_to: Optional[int] = None,
) -> "pd.DataFrame":
    params: Dict[str, object] = {}
    if updated_from is not None:
        params["filter[updated_at][from]"] = int(updated_from)
    if updated_to is not None:
        params["filter[updated_at][to]"] = int(updated_to)

    items = fetch_entities(client, "/api/v4/tasks", params=params)
    rows: List[Dict[str, object]] = []
    for task in items:
        row: Dict[str, object] = {
            "id": task.get("id"),
            "entity_id": task.get("entity_id"),
            "entity_type": task.get("entity_type"),
            "responsible_user_id": task.get("responsible_user_id"),
            "created_by": task.get("created_by"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "complete_till": task.get("complete_till"),
            "is_completed": task.get("is_completed"),
            "text": task.get("text"),
            "result": task.get("result"),
        }
        row["responsible_user"] = map_user(row.get("responsible_user_id"))
        row["created_by_user"] = map_user(row.get("created_by"))
        row = enrich_datetime_columns(row, ["created_at", "updated_at", "complete_till"])
        rows.append(row)
    return to_dataframe(rows)
