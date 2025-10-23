from __future__ import annotations

from typing import Dict, List, Optional

from ..client import AmoCRMClient
from ..meta import flatten_custom_fields, map_loss_reason, map_status, map_user
from .base import enrich_datetime_columns, fetch_entities, to_dataframe


def fetch_leads(
    client: AmoCRMClient,
    *,
    updated_from: Optional[int] = None,
    updated_to: Optional[int] = None,
) -> "pd.DataFrame":
    params: Dict[str, object] = {"with": "contacts"}
    if updated_from is not None:
        params["filter[updated_at][from]"] = int(updated_from)
    if updated_to is not None:
        params["filter[updated_at][to]"] = int(updated_to)

    items = fetch_entities(client, "/api/v4/leads", params=params)
    rows: List[Dict[str, object]] = []
    for lead in items:
        row: Dict[str, object] = {
            "id": lead.get("id"),
            "name": lead.get("name"),
            "price": lead.get("price"),
            "responsible_user_id": lead.get("responsible_user_id"),
            "created_at": lead.get("created_at"),
            "updated_at": lead.get("updated_at"),
            "closed_at": lead.get("closed_at"),
            "status_id": lead.get("status_id"),
            "pipeline_id": lead.get("pipeline_id"),
            "loss_reason_id": lead.get("loss_reason_id"),
        }
        status_info = map_status(row["status_id"])
        row["status_name"] = status_info.get("status_name")
        row["pipeline_name"] = status_info.get("pipeline_name")
        row["loss_reason"] = map_loss_reason(row.get("loss_reason_id"))
        row["responsible_user"] = map_user(row.get("responsible_user_id"))
        embedded = lead.get("_embedded") if isinstance(lead, dict) else None
        if isinstance(embedded, dict):
            tags = embedded.get("tags")
            if isinstance(tags, list):
                row["tags"] = ", ".join(str(tag.get("name")) for tag in tags if tag.get("name"))
        row.update(flatten_custom_fields("leads", lead.get("custom_fields_values")))
        row = enrich_datetime_columns(row, ["created_at", "updated_at", "closed_at"])
        rows.append(row)
    return to_dataframe(rows)
