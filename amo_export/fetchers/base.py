from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import pandas as pd

from ..client import AmoCRMClient
from ..logging_utils import get_logger

LOGGER = get_logger(__name__)


def timestamp_to_iso(timestamp: Optional[object]) -> Optional[str]:
    if timestamp in (None, "", 0):
        return None
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def enrich_datetime_columns(row: Dict[str, object], columns: Iterable[str]) -> Dict[str, object]:
    enriched = dict(row)
    for column in columns:
        value = row.get(column)
        enriched[f"{column}_dt"] = timestamp_to_iso(value)
    return enriched


def fetch_entities(
    client: AmoCRMClient,
    path: str,
    params: Optional[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    LOGGER.info("Fetching %s", path)
    return list(client.get_paginated(path, params=params))


def to_dataframe(rows: List[Dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
