"""Lightweight Supabase REST client used for persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


class SupabaseError(RuntimeError):
    """Raised when Supabase returns an unexpected response."""


@dataclass
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
        import logging
        import json
        logger = logging.getLogger(__name__)

        # Log the full payload for debugging (truncated)
        payload_preview = {k: (str(v)[:100] if isinstance(v, str) else v) for k, v in payload.items()}
        logger.debug(
            "🔍 Supabase insert 请求 - table=%s, payload_keys=%s, payload_preview=%s",
            table,
            list(payload.keys()),
            json.dumps(payload_preview, ensure_ascii=False, default=str)[:500],
        )

        try:
            response = await self._request(
                "POST",
                table,
                json=[payload],
                headers={"Prefer": "return=representation"},
            )
        except SupabaseError as exc:
            logger.error(
                "❌ Supabase insert 请求失败 - table=%s, error=%s, payload_keys=%s",
                table,
                str(exc),
                list(payload.keys()),
            )
            raise

        # Log the full response for debugging
        logger.debug(
            "🔍 Supabase insert 响应 - table=%s, response_type=%s, response_length=%s",
            table,
            type(response).__name__,
            len(response) if isinstance(response, (list, dict)) else 0,
        )

        if isinstance(response, list) and response:
            record = response[0]
            # 检查返回的 record 是否包含有效的 id
            if isinstance(record, dict):
                record_id = record.get("id")
                if record_id is None:
                    logger.error(
                        "❌ Supabase insert 返回 id=None (list[0]) - table=%s, record_keys=%s, payload_keys=%s, record_values=%s",
                        table,
                        list(record.keys()) if record else None,
                        list(payload.keys()),
                        json.dumps({k: (str(v)[:100] if isinstance(v, str) else v) for k, v in record.items()}, ensure_ascii=False, default=str)[:1000],
                    )
                else:
                    logger.debug("✅ Supabase insert 成功 - table=%s, id=%s", table, record_id)
                return record
            return record
        if isinstance(response, dict):
            record_id = response.get("id")
            if record_id is None:
                logger.error(
                    "❌ Supabase insert 返回 id=None (dict) - table=%s, record_keys=%s, payload_keys=%s, record_values=%s",
                    table,
                    list(response.keys()) if response else None,
                    list(payload.keys()),
                    json.dumps({k: (str(v)[:100] if isinstance(v, str) else v) for k, v in response.items()}, ensure_ascii=False, default=str)[:1000],
                )
            else:
                logger.debug("✅ Supabase insert 成功 - table=%s, id=%s", table, record_id)
            return response

        # Log unexpected response format
        logger.error(
            "❌ Supabase insert 返回意外格式 - table=%s, response_type=%s, response=%s, payload_keys=%s",
            table,
            type(response).__name__,
            str(response)[:500] if response else None,
            list(payload.keys()),
        )
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

    async def rpc(self, function_name: str, params: Dict[str, Any]) -> Any:
        """Call a PostgreSQL function via RPC."""
        response = await self._request(
            "POST",
            f"rpc/{function_name}",
            json=params,
        )
        return response

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        import logging
        logger = logging.getLogger(__name__)
        
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
            error_text = response.text.strip()
            logger.error(
                "❌ Supabase HTTP 错误 - method=%s, path=%s, status=%d, error=%s",
                method,
                path,
                response.status_code,
                error_text[:500] if error_text else "空响应",
            )
            raise SupabaseError(
                f"Supabase error {response.status_code}: {error_text}"
            )

        if response.headers.get("Content-Type", "").startswith("application/json"):
            json_response = response.json()
            # 检查响应中是否包含错误信息（某些情况下 Supabase 可能在 200 响应中返回错误）
            if isinstance(json_response, dict):
                # 检查常见的错误字段
                if "error" in json_response or "message" in json_response:
                    error_msg = json_response.get("error") or json_response.get("message", "")
                    if error_msg:
                        logger.warning(
                            "⚠️ Supabase 响应包含错误信息 - method=%s, path=%s, error=%s",
                            method,
                            path,
                            str(error_msg)[:200],
                        )
            return json_response
        return None


_CLIENT_CACHE: dict[tuple[str, str], SupabaseClient] = {}


def get_supabase_client(
    url: str,
    service_key: str,
    *,
    timeout: float = 8.0,
) -> SupabaseClient:
    """Return a cached Supabase client instance for the given credentials."""

    identifier = (url.strip(), service_key.strip())

    if not identifier[0] or not identifier[1]:
        raise SupabaseError("Supabase URL and service key are required")

    client = _CLIENT_CACHE.get(identifier)
    if client is None:
        client = SupabaseClient(rest_url=identifier[0], service_key=identifier[1], timeout=timeout)
        _CLIENT_CACHE[identifier] = client

    return client
