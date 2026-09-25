"""ClickUp REST API client for Dawnlist sync.

Uses a personal API token stored in keyring. Handles rate limiting
(100 req/min for personal tokens) and pagination.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

import keyring

CLICKUP_SERVICE = "dawnlist-clickup"
CLICKUP_ACCOUNT = "api-token"
BASE_URL = "https://api.clickup.com/api/v2"


class ClickUpError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"ClickUp {status}: {message}")


class ClickUpClient:
    """Thin REST client for ClickUp v2 API."""

    def __init__(self, token: str | None = None):
        self._token = token or _stored_token()
        if not self._token:
            raise ClickUpError(0, "No ClickUp API token configured")
        self._last_request_at: float = 0
        self._min_interval: float = 0.6  # ~100/min

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> dict:
        return self._request("POST", path, body=body)

    def put(self, path: str, body: dict | None = None) -> dict:
        return self._request("PUT", path, body=body)

    def get_task(self, task_id: str) -> dict:
        return self.get(f"/task/{task_id}",
                        params={"custom_task_ids": "false",
                                "include_subtasks": "true"})

    def get_list_tasks(self, list_id: str, page: int = 0,
                       subtasks: bool = True,
                       statuses: list[str] | None = None,
                       include_closed: bool = False,
                       ) -> dict:
        params: dict[str, Any] = {
            "page": str(page),
            "subtasks": str(subtasks).lower(),
            "include_closed": str(include_closed).lower(),
        }
        if statuses:
            for s in statuses:
                params.setdefault("statuses[]", [])
                if isinstance(params["statuses[]"], list):
                    params["statuses[]"].append(s)
        return self.get(f"/list/{list_id}/task", params=params)

    def iter_list_tasks(self, list_id: str, **kwargs) -> Iterator[dict]:
        """Paginate through all tasks in a list."""
        page = 0
        while True:
            result = self.get_list_tasks(list_id, page=page, **kwargs)
            tasks = result.get("tasks", [])
            if not tasks:
                break
            yield from tasks
            if result.get("last_page", True):
                break
            page += 1

    def _request(self, method: str, path: str,
                 params: dict | None = None,
                 body: dict | None = None) -> dict:
        self._throttle()
        url = BASE_URL + path
        if params:
            parts = []
            for k, v in params.items():
                if isinstance(v, list):
                    for item in v:
                        parts.append(f"{k}={urllib.request.quote(str(item))}")
                else:
                    parts.append(
                        f"{k}={urllib.request.quote(str(v))}")
            url += "?" + "&".join(parts)

        from app.core.http import build_request

        data = json.dumps(body).encode() if body else None
        req = build_request(url, data=data, method=method,
                            headers={"Authorization": self._token,
                                     "Content-Type": "application/json"})

        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = int(
                        e.headers.get("Retry-After", "10"))
                    time.sleep(min(retry_after, 60))
                    continue
                if 500 <= e.code < 600 and attempt < 2:
                    time.sleep(2)
                    continue
                body_text = e.read().decode()[:500]
                raise ClickUpError(e.code, body_text) from None
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise ClickUpError(0, str(e)) from None

        raise ClickUpError(429, "Rate limit retries exhausted")

    def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()


def _stored_token() -> str | None:
    return keyring.get_password(CLICKUP_SERVICE, CLICKUP_ACCOUNT)


def has_token() -> bool:
    return _stored_token() is not None
