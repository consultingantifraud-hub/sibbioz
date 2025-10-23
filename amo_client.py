"""AmoCRM API client with token refresh support."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import requests

import config


@dataclass
class AmoTokenStore:
    """Handle amoCRM access token refresh and API requests."""

    path: str

    def __post_init__(self) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            self.t: Dict[str, Any] = json.load(f)
        self._validate_config()
        self.base = "https://" + self.t["base_domain"]
        self._update_headers()

    def _validate_config(self) -> None:
        required = ["base_domain", "client_id", "client_secret", "redirect_uri"]
        missing = [key for key in required if not self.t.get(key)]
        if missing:
            raise ValueError(f"Missing keys in token file: {', '.join(missing)}")

    def _update_headers(self) -> None:
        self.headers = {"Authorization": "Bearer " + str(self.t.get("access_token", ""))}

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.t, f, ensure_ascii=False, indent=2)

    def _auth_code_exchange(self) -> None:
        code = self.t.get("auth_code_backup")
        if not code:
            raise RuntimeError("auth_code_backup not found in tokens file")
        payload = {
            "client_id": self.t["client_id"],
            "client_secret": self.t["client_secret"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.t["redirect_uri"],
        }
        response = requests.post(
            self.base + "/oauth2/access_token", json=payload, timeout=config.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        self._update_tokens(response.json())

    def _refresh(self) -> None:
        if not self.t.get("refresh_token"):
            self._auth_code_exchange()
            return
        payload = {
            "client_id": self.t["client_id"],
            "client_secret": self.t["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": self.t["refresh_token"],
            "redirect_uri": self.t["redirect_uri"],
        }
        response = requests.post(
            self.base + "/oauth2/access_token", json=payload, timeout=config.REQUEST_TIMEOUT
        )
        if response.status_code >= 400:
            self._auth_code_exchange()
        else:
            self._update_tokens(response.json())

    def _update_tokens(self, data: Dict[str, Any]) -> None:
        self.t.update(
            {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "expires_in": data.get("expires_in", self.t.get("expires_in", 0)),
                "token_type": data.get("token_type", self.t.get("token_type", "Bearer")),
                "created_at": int(time.time()),
            }
        )
        self.save()
        self._update_headers()

    def request_with_refresh(self, fn: Callable[[], requests.Response]) -> requests.Response:
        response = fn()
        if response.status_code != 401:
            return response
        self._refresh()
        return fn()

    def ensure_valid(self) -> bool:
        def _call() -> requests.Response:
            return requests.get(
                self.base + "/api/v4/account",
                headers=self.headers,
                timeout=config.REQUEST_TIMEOUT,
            )

        response = self.request_with_refresh(_call)
        response.raise_for_status()
        print("Auth OK for account:", response.json().get("name"))
        return True

    def get_paged_cap(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        limit: int = config.REQUEST_LIMIT,
        cap: int = config.SAMPLE_LIMIT,
        timeout_sec: Optional[int] = None,
    ) -> list[Dict[str, Any]]:
        timeout_value = timeout_sec or config.REQUEST_TIMEOUT
        items: list[Dict[str, Any]] = []
        page = 1
        while len(items) < cap:
            query = dict(params or {})
            query["page"] = page
            query["limit"] = min(limit, cap - len(items))
            url = self.base + path

            def _call() -> requests.Response:
                return requests.get(url, params=query, headers=self.headers, timeout=timeout_value)

            response = self.request_with_refresh(_call)
            if response.status_code >= 400:
                print("ERROR:", response.status_code, response.text[:400])
                response.raise_for_status()
            data = response.json()
            embedded = data.get("_embedded", {})
            chunk = None
            for value in embedded.values():
                if isinstance(value, list):
                    chunk = value
                    break
            if not chunk:
                break
            items.extend(chunk)
            if not data.get("_links", {}).get("next"):
                break
            page += 1
        return items
