from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import requests

from .logging_utils import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class TokenInfo:
    access_token: str
    base_domain: str
    api_domain: Optional[str] = None

    @classmethod
    def load(cls, path: Path) -> "TokenInfo":
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        access_token = str(data.get("access_token"))
        base_domain = str(data.get("base_domain"))
        api_domain = data.get("api_domain")
        if not access_token:
            raise ValueError("tokens file does not contain access_token")
        if not base_domain:
            raise ValueError("tokens file does not contain base_domain")
        return cls(access_token=access_token, base_domain=base_domain, api_domain=api_domain)

    @property
    def api_base_url(self) -> str:
        domain = self.api_domain or self.base_domain
        domain = domain.strip().rstrip("/")
        if domain.startswith("http://") or domain.startswith("https://"):
            return domain
        return f"https://{domain}"


class AmoCRMClient:
    """Thin wrapper over the amoCRM HTTP API."""

    def __init__(
        self,
        token_info: TokenInfo,
        *,
        timeout: int = 30,
        page_size: int = 250,
    ) -> None:
        self._token_info = token_info
        self._timeout = timeout
        self._page_size = page_size
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token_info.access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    @property
    def base_url(self) -> str:
        return self._token_info.api_base_url

    def get(self, path: str, params: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        url = self._build_url(path)
        LOGGER.debug("GET %s params=%s", url, params)
        response = self._session.get(url, params=params, timeout=self._timeout)
        if response.status_code >= 400:
            raise RuntimeError(f"GET {path} failed with status {response.status_code}: {response.text}")
        payload: Dict[str, object] = response.json()
        return payload

    def get_paginated(
        self,
        path: str,
        params: Optional[Dict[str, object]] = None,
    ) -> Iterator[Dict[str, object]]:
        page = 1
        params = dict(params or {})
        while True:
            params_with_paging = dict(params)
            params_with_paging.setdefault("limit", self._page_size)
            params_with_paging["page"] = page
            data = self.get(path, params=params_with_paging)
            batch = self._extract_items(data)
            if not batch:
                break
            for item in batch:
                yield item
            if not self._has_next(data) or len(batch) < params_with_paging["limit"]:
                break
            page += 1

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        path = path.lstrip("/")
        return f"{self.base_url}/{path}"

    @staticmethod
    def _extract_items(payload: Dict[str, object]) -> List[Dict[str, object]]:
        embedded = payload.get("_embedded") if isinstance(payload, dict) else None
        items: List[Dict[str, object]] = []
        if isinstance(embedded, dict):
            for value in embedded.values():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            items.append(item)
        return items

    @staticmethod
    def _has_next(payload: Dict[str, object]) -> bool:
        links = payload.get("_links") if isinstance(payload, dict) else None
        if not isinstance(links, dict):
            return False
        return bool(links.get("next"))


def iter_paginated(
    client: AmoCRMClient,
    path: str,
    params: Optional[Dict[str, object]] = None,
) -> Iterator[Dict[str, object]]:
    """Convenience wrapper returning an iterator over paginated results."""

    yield from client.get_paginated(path, params=params)
