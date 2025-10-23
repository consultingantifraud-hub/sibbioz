from __future__ import annotations

from typing import Dict, List, Optional

from ..client import AmoCRMClient
from ..meta import flatten_custom_fields, map_user
from .base import enrich_datetime_columns, fetch_entities, to_dataframe


def fetch_contacts(
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

    items = fetch_entities(client, "/api/v4/contacts", params=params)
    rows: List[Dict[str, object]] = []
    for contact in items:
        row: Dict[str, object] = {
            "id": contact.get("id"),
            "name": contact.get("name"),
            "first_name": contact.get("first_name"),
            "last_name": contact.get("last_name"),
            "responsible_user_id": contact.get("responsible_user_id"),
            "created_at": contact.get("created_at"),
            "updated_at": contact.get("updated_at"),
        }
        row["responsible_user"] = map_user(row.get("responsible_user_id"))
        embedded = contact.get("_embedded") if isinstance(contact, dict) else None
        if isinstance(embedded, dict):
            tags = embedded.get("tags")
            if isinstance(tags, list):
                row["tags"] = ", ".join(str(tag.get("name")) for tag in tags if tag.get("name"))
            companies = embedded.get("companies")
            if isinstance(companies, list) and companies:
                row["company_id"] = companies[0].get("id")
                row["company_name"] = companies[0].get("name")
        row.update(flatten_custom_fields("contacts", contact.get("custom_fields_values")))
        row = enrich_datetime_columns(row, ["created_at", "updated_at"])
        rows.append(row)
    return to_dataframe(rows)
