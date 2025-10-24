"""AmoCRM API client helpers with automatic token refresh and pagination."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


def _ensure_base_url(base_domain: str) -> str:
    base_domain = base_domain.strip()
    if base_domain.startswith("http"):
        return base_domain.rstrip("/")
    return f"https://{base_domain}".rstrip("/")


@dataclass
class AmoTokenStore:
    """Simple token holder with refresh support and pagination helpers."""

    tokens_path: Path
    request_timeout: int = 30
    request_limit: int = 250
    auth_code_backup: Optional[Path] = None

    def __post_init__(self) -> None:
        self.tokens_path = Path(self.tokens_path)
        if self.auth_code_backup is not None:
            self.auth_code_backup = Path(self.auth_code_backup)
        self._tokens: Dict[str, Any] = {}
        self._session: Optional[requests.Session] = None
        self._base_url: Optional[str] = None
        self._load_tokens()
        self._ensure_session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        if not self._base_url:
            base_domain = self._tokens.get("base_domain")
            if not base_domain:
                raise ValueError("Missing 'base_domain' in tokens file")
            self._base_url = _ensure_base_url(str(base_domain))
        return self._base_url

    @property
    def session(self) -> requests.Session:
        self._ensure_session()
        assert self._session is not None
        return self._session

    def get_paged_cap(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        cap: Optional[int] = None,
        on_page: Optional[Callable[[int, int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch a collection from amoCRM with pagination.

        Args:
            path: API path starting with ``/`` (e.g. ``/leads``).
            params: Optional query parameters.
            limit: Page size (defaults to ``request_limit``).
            cap: Maximum number of entities to return. ``None`` for unlimited.
            on_page: Optional callback receiving ``(page, total_items, last_page_size)``.
        """

        aggregated: List[Dict[str, Any]] = []
        page = 1
        limit = limit or self.request_limit
        params = dict(params or {})

        while True:
            query = dict(params)
            query.setdefault("limit", limit)
            query["page"] = page
            response = self._request("GET", path, params=query)
            data = response.json()
            batch: List[Dict[str, Any]] = []
            if isinstance(data, dict):
                embedded = data.get("_embedded")
                if isinstance(embedded, dict):
                    for value in embedded.values():
                        if isinstance(value, list):
                            batch.extend(value)
            elif isinstance(data, list):
                batch = data

            if not batch:
                break

            aggregated.extend(batch)
            if on_page:
                on_page(page, len(aggregated), len(batch))

            if cap is not None and len(aggregated) >= cap:
                del aggregated[cap:]
                break

            if len(batch) < limit:
                break
            page += 1

        return aggregated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_tokens(self) -> None:
        if not self.tokens_path.exists():
            raise FileNotFoundError(f"Tokens file not found: {self.tokens_path}")
        data = json.loads(self.tokens_path.read_text(encoding="utf-8"))
        if "access_token" not in data:
            raise ValueError("Tokens file must contain 'access_token'")
        self._tokens = data
        self._base_url = None

    def _ensure_session(self) -> None:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "Authorization": f"Bearer {self._tokens.get('access_token')}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
            )

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> requests.Response:
        url = f"{self.base_url}/api/v4{path}"
        session = self.session
        response = session.request(
            method,
            url,
            params=params,
            json=json_payload,
            timeout=self.request_timeout,
        )
        if response.status_code == 401 and retry:
            logger.info("Received 401 from %s, trying to refresh token", path)
            if self._refresh_tokens():
                logger.info("token refresh -> ok")
                return self._request(method, path, params=params, json_payload=json_payload, retry=False)
            logger.error("token refresh failed; response text: %s", response.text)
        response.raise_for_status()
        return response

    def _refresh_tokens(self) -> bool:
        token_data = self._tokens
        client_id = token_data.get("client_id") or os.getenv("AMO_CLIENT_ID")
        client_secret = token_data.get("client_secret") or os.getenv("AMO_CLIENT_SECRET")
        redirect_uri = token_data.get("redirect_uri") or os.getenv("AMO_REDIRECT_URI")
        refresh_token = token_data.get("refresh_token") or os.getenv("AMO_REFRESH_TOKEN")
        if client_id and client_secret and redirect_uri and refresh_token:
            payload = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": redirect_uri,
            }
            url = f"{self.base_url}/oauth2/access_token"
            response = requests.post(url, data=payload, timeout=self.request_timeout)
            if response.ok:
                data = response.json()
                token_data.update(data)
                self.tokens_path.write_text(json.dumps(token_data, ensure_ascii=False, indent=2), encoding="utf-8")
                self._session = None
                self._ensure_session()
                return True
            logger.error("Failed to refresh token: %s", response.text)

        if self.auth_code_backup and self.auth_code_backup.exists():
            auth_code = self.auth_code_backup.read_text(encoding="utf-8").strip()
            if auth_code:
                client_id = client_id or token_data.get("client_id") or os.getenv("AMO_CLIENT_ID")
                client_secret = client_secret or token_data.get("client_secret") or os.getenv("AMO_CLIENT_SECRET")
                redirect_uri = redirect_uri or token_data.get("redirect_uri") or os.getenv("AMO_REDIRECT_URI")
                if client_id and client_secret and redirect_uri:
                    payload = {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "authorization_code",
                        "code": auth_code,
                        "redirect_uri": redirect_uri,
                    }
                    url = f"{self.base_url}/oauth2/access_token"
                    response = requests.post(url, data=payload, timeout=self.request_timeout)
                    if response.ok:
                        data = response.json()
                        token_data.update(data)
                        self.tokens_path.write_text(json.dumps(token_data, ensure_ascii=False, indent=2), encoding="utf-8")
                        self._session = None
                        self._ensure_session()
                        return True
                    logger.error("Failed to exchange auth code for tokens: %s", response.text)
        return False


def rate_limit_pause(retry_after_header: Optional[str]) -> None:
    """Pause when amoCRM asks for retry."""
    if not retry_after_header:
        return
    try:
        retry_after = float(retry_after_header)
    except (TypeError, ValueError):
        return
    time.sleep(max(retry_after, 0))

