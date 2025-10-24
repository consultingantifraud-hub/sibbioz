"""Configuration for amoCRM export utilities."""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

TOKENS_FILE = BASE_DIR / "tokens_sibbioz.json"
OUT_XLSX = BASE_DIR / "amo_sibbioz_export.xlsx"
REQUEST_LIMIT = 250
REQUEST_TIMEOUT = 25
DAYS_BACK_EVENTS = 360
DAYS_BACK_NOTES = 360
DOWNLOAD_LEADS = True
DOWNLOAD_CONTACTS = True
DOWNLOAD_COMPANIES = True
DOWNLOAD_COMMENTS = True
SAMPLE_LIMIT = 500
USE_RICH_PROGRESS = True
PERIOD_DAYS_DEFAULT = 365

# Maximum number of rows to keep when exporting to CSV (per entity)
CSV_ROW_LIMIT = 500
