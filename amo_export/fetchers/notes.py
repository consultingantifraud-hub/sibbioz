from __future__ import annotations

from typing import Dict, List, Optional

from ..client import AmoCRMClient
from ..meta import map_user
from .base import enrich_datetime_columns, fetch_entities, to_dataframe


NOTE_ENDPOINTS = [
    "/api/v4/leads/notes",
    "/api/v4/contacts/notes",
    "/api/v4/companies/notes",
]


def fetch_notes(
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

    rows: List[Dict[str, object]] = []
    for endpoint in NOTE_ENDPOINTS:
        items = fetch_entities(client, endpoint, params=params)
        for note in items:
            row: Dict[str, object] = {
                "id": note.get("id"),
                "entity_id": note.get("entity_id"),
                "entity_type": note.get("entity_type"),
                "created_by": note.get("created_by"),
                "responsible_user_id": note.get("responsible_user_id"),
                "note_type": note.get("note_type"),
                "text": note.get("text"),
                "created_at": note.get("created_at"),
                "updated_at": note.get("updated_at"),
            }
            row["responsible_user"] = map_user(row.get("responsible_user_id"))
            row["created_by_user"] = map_user(row.get("created_by"))
            row = enrich_datetime_columns(row, ["created_at", "updated_at"])
            rows.append(row)
    return to_dataframe(rows)
