"""Lightweight Supabase REST client used for persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


class SupabaseError(RuntimeError):
    """Raised when Supabase returns an unexpected response."""


@dataclass(slots=True)
class SupabaseClient:
    """Minimal REST client for Supabase tables."""

    rest_url: str
    service_key: str
    timeout: float = 8.0

    def __post_init__(self) -> None:
        self.rest_url = self.rest_url.rstrip("/")
        if not self.rest_url.endswith("/rest/v1"):
            self.rest_url = f"{self.rest_url}/rest/v1"
        if not self.service_key:
            raise SupabaseError("Supabase service key is required")

    async def insert(self, table: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        response = await self._request(
            "POST",
            table,
            json=[payload],
            headers={"Prefer": "return=representation"},
        )
        if isinstance(response, list) and response:
            return response[0]
        if isinstance(response, dict):
            return response
        return None

    async def upsert(self, table: str, payload: Dict[str, Any], *, on_conflict: str) -> Optional[Dict[str, Any]]:
        response = await self._request(
            "POST",
            table,
            json=[payload],
            headers={
                "Prefer": f"return=representation,resolution=merge-duplicates",
            },
            params={"on_conflict": on_conflict},
        )
        if isinstance(response, list) and response:
            return response[0]
        if isinstance(response, dict):
            return response
        return None

    async def select_one(
        self,
        table: str,
        *,
        filters: Dict[str, str],
        columns: str = "*",
    ) -> Optional[Dict[str, Any]]:
        params = {key: f"eq.{value}" for key, value in filters.items() if value is not None}
        params["select"] = columns
        params["limit"] = 1
        response = await self._request("GET", table, params=params)
        if isinstance(response, list) and response:
            return response[0]
        if isinstance(response, dict):
            return response
        return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        url = f"{self.rest_url}/{path.lstrip('/')}"
        request_headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(method, url, headers=request_headers, **kwargs)
            except httpx.TimeoutException as exc:  # pragma: no cover - network
                raise SupabaseError("Supabase request timed out") from exc
            except httpx.HTTPError as exc:  # pragma: no cover - network
                raise SupabaseError(f"Supabase request failed: {exc}") from exc

        if response.status_code >= 400:
            raise SupabaseError(
                f"Supabase error {response.status_code}: {response.text.strip()}"
            )

        if response.headers.get("Content-Type", "").startswith("application/json"):
            return response.json()
        return None
_CLIENT_INSTANCE: SupabaseClient | None = None


def get_supabase_client(
    url: str,
    service_key: str,
    *,
    timeout: float = 8.0,
) -> SupabaseClient:
    """Return a shared Supabase client instance."""

    global _CLIENT_INSTANCE  # noqa: PLW0603
    if _CLIENT_INSTANCE is None:
        if not url or not service_key:
            raise SupabaseError("Supabase URL and service key are required")
        _CLIENT_INSTANCE = SupabaseClient(rest_url=url, service_key=service_key, timeout=timeout)
    return _CLIENT_INSTANCE
