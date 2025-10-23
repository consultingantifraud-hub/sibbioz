from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Optional

import pandas as pd
import yaml

from .logging_utils import get_logger

LOGGER = get_logger(__name__)


def load_fields_map(path: Optional[Path]) -> Dict[str, object]:
    if path is None or not path.exists():
        return {}
    LOGGER.info("Loading fields map from %s", path)
    with path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp)
    return data or {}


def apply_fields_map(df: pd.DataFrame, entity: str, fields_map: Mapping[str, object]) -> pd.DataFrame:
    if df.empty:
        return df
    entity_map = fields_map.get(entity)
    if not isinstance(entity_map, Mapping):
        return df
    result = df.copy()
    rename_map = entity_map.get("rename") if isinstance(entity_map, Mapping) else None
    if isinstance(rename_map, Mapping) and rename_map:
        LOGGER.info("Applying rename for entity %s: %s", entity, list(rename_map.items()))
        result = result.rename(columns=dict(rename_map))
    drop_list = entity_map.get("drop") if isinstance(entity_map, Mapping) else None
    if isinstance(drop_list, list) and drop_list:
        LOGGER.info("Dropping columns for entity %s: %s", entity, drop_list)
        result = result.drop(columns=[col for col in drop_list if col in result.columns], errors="ignore")
    return result


def write_excel(
    datasets: Mapping[str, pd.DataFrame],
    output_path: Path,
    *,
    save_csv: bool = False,
    fields_map: Optional[Mapping[str, object]] = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Writing Excel workbook to %s", output_path)
    effective_map = fields_map or {}
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for entity, df in datasets.items():
            if df is None or df.empty:
                LOGGER.warning("Sheet %s is empty; skipping", entity)
                continue
            sheet_df = apply_fields_map(df, entity, effective_map)
            sheet_name = entity.capitalize()
            sheet_df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    if save_csv:
        for entity, df in datasets.items():
            if df is None or df.empty:
                continue
            sheet_df = apply_fields_map(df, entity, effective_map)
            csv_path = output_path.with_name(f"{output_path.stem}_{entity}.csv")
            LOGGER.info("Saving CSV for %s to %s", entity, csv_path)
            sheet_df.to_csv(csv_path, index=False)
