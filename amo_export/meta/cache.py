from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from ..client import AmoCRMClient
from ..logging_utils import get_logger
from . import discovery

LOGGER = get_logger(__name__)


CACHE_DIR = Path(".cache")


@dataclass
class MetadataBundle:
    pipelines: Dict[int, Dict[str, object]]
    statuses: Dict[int, Dict[str, object]]
    loss_reasons: Dict[int, str]
    users: Dict[int, str]
    fields_leads: Dict[int, Dict[str, object]]
    fields_contacts: Dict[int, Dict[str, object]]
    fields_companies: Dict[int, Dict[str, object]]

    @property
    def custom_fields(self) -> Dict[str, Dict[int, Dict[str, object]]]:
        return {
            "leads": self.fields_leads,
            "contacts": self.fields_contacts,
            "companies": self.fields_companies,
        }


_CACHE_FILES = {
    "pipelines": CACHE_DIR / "pipelines.json",
    "statuses": CACHE_DIR / "statuses.json",
    "loss_reasons": CACHE_DIR / "loss_reasons.json",
    "users": CACHE_DIR / "users.json",
    "fields_leads": CACHE_DIR / "fields_leads.json",
    "fields_contacts": CACHE_DIR / "fields_contacts.json",
    "fields_companies": CACHE_DIR / "fields_companies.json",
}


def _ensure_cache_dir() -> None:
    if not CACHE_DIR.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if isinstance(data, dict):
        return data
    raise ValueError(f"Unexpected cache structure in {path}")


def _write_json(path: Path, data: Dict) -> None:
    _ensure_cache_dir()
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def _convert_keys_to_int(data: Dict) -> Dict[int, object]:
    result: Dict[int, object] = {}
    for key, value in data.items():
        try:
            int_key = int(key)
        except (TypeError, ValueError):
            continue
        result[int_key] = value
    return result


def _maybe_load(name: str) -> Dict[int, object]:
    path = _CACHE_FILES[name]
    if not path.exists():
        return {}
    try:
        loaded = _load_json(path)
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.warning("Failed to read cache %s: %s", path, exc)
        return {}
    return _convert_keys_to_int(loaded)


def _save(name: str, data: Dict[int, object]) -> None:
    path = _CACHE_FILES[name]
    serializable = {str(key): value for key, value in data.items()}
    _write_json(path, serializable)


def load_metadata(client: AmoCRMClient, *, force_refresh: bool = False) -> MetadataBundle:
    """Load metadata from cache or amoCRM."""

    LOGGER.info("Loading amoCRM metadata (force_refresh=%s)", force_refresh)
    if force_refresh:
        for path in _CACHE_FILES.values():
            if path.exists():
                path.unlink()

    pipelines_data = _maybe_load("pipelines")
    statuses_data = _maybe_load("statuses")
    loss_reasons_data = _maybe_load("loss_reasons")
    users_data = _maybe_load("users")
    fields_leads = _maybe_load("fields_leads")
    fields_contacts = _maybe_load("fields_contacts")
    fields_companies = _maybe_load("fields_companies")

    if not pipelines_data or not statuses_data:
        discovered = discovery.discover_pipelines(client)
        pipelines_data = discovered["pipelines"]
        statuses_data = discovered["statuses"]
        _save("pipelines", pipelines_data)
        _save("statuses", statuses_data)
    if not loss_reasons_data:
        loss_reasons_data = discovery.discover_loss_reasons(client)
        _save("loss_reasons", loss_reasons_data)
    if not users_data:
        users_data = discovery.discover_users(client)
        _save("users", users_data)
    if not fields_leads:
        fields_leads = discovery.discover_custom_fields(client, "leads")
        _save("fields_leads", fields_leads)
    if not fields_contacts:
        fields_contacts = discovery.discover_custom_fields(client, "contacts")
        _save("fields_contacts", fields_contacts)
    if not fields_companies:
        fields_companies = discovery.discover_custom_fields(client, "companies")
        _save("fields_companies", fields_companies)

    return MetadataBundle(
        pipelines=pipelines_data,
        statuses=statuses_data,
        loss_reasons=loss_reasons_data,
        users=users_data,
        fields_leads=fields_leads,
        fields_contacts=fields_contacts,
        fields_companies=fields_companies,
    )
