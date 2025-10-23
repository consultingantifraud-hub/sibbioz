from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .client import AmoCRMClient, TokenInfo
from .excel_writer import load_fields_map, write_excel
from .fetchers import companies, contacts, leads, notes, tasks
from .logging_utils import setup_logging
from .meta import configure_mapping, load_metadata


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="amoCRM data exporter")
    parser.add_argument("--out", required=True, help="Path to the resulting XLSX file")
    parser.add_argument("--tokens", help="Path to tokens JSON file")
    parser.add_argument("--days", type=int, default=30, help="Number of days to look back (default: 30)")
    parser.add_argument("--dump-meta", action="store_true", help="Refresh metadata cache before export")
    parser.add_argument("--save-csv", action="store_true", help="Save per-sheet CSV files alongside Excel")
    parser.add_argument(
        "--fields-map",
        help="Path to YAML configuration for renaming/dropping columns",
    )
    return parser.parse_args(argv)


def resolve_tokens_path(provided: Optional[str]) -> Path:
    if provided:
        path = Path(provided)
        if not path.exists():
            raise FileNotFoundError(f"Tokens file not found: {path}")
        return path
    for candidate in ("tokens.json", "tokens_sibbioz.json"):
        path = Path(candidate)
        if path.exists():
            return path
    raise FileNotFoundError("Tokens file not provided and default tokens.json not found")


def resolve_fields_map_path(provided: Optional[str]) -> Optional[Path]:
    if provided:
        path = Path(provided)
        if not path.exists():
            raise FileNotFoundError(f"Fields map file not found: {path}")
        return path
    default = Path("fields_map.yaml")
    return default if default.exists() else None


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging()
    tokens_path = resolve_tokens_path(args.tokens)
    token_info = TokenInfo.load(tokens_path)
    client = AmoCRMClient(token_info)

    metadata = load_metadata(client, force_refresh=args.dump_meta)
    configure_mapping(metadata)

    now_ts = int(time.time())
    updated_from = now_ts - max(args.days, 0) * 24 * 3600 if args.days else None

    datasets: Dict[str, pd.DataFrame] = {}
    datasets["leads"] = leads.fetch_leads(client, updated_from=updated_from)
    datasets["contacts"] = contacts.fetch_contacts(client, updated_from=updated_from)
    datasets["companies"] = companies.fetch_companies(client, updated_from=updated_from)
    datasets["tasks"] = tasks.fetch_tasks(client, updated_from=updated_from)
    datasets["notes"] = notes.fetch_notes(client, updated_from=updated_from)

    fields_map_path = resolve_fields_map_path(args.fields_map)
    fields_map = load_fields_map(fields_map_path)

    output_path = Path(args.out)
    write_excel(datasets, output_path, save_csv=args.save_csv, fields_map=fields_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
