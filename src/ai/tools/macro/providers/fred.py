"""FRED-backed macro data provider implementation."""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

from ...base import ToolResult
from ...exceptions import ToolRateLimitError
from .base import MacroProvider
from src.utils import setup_logger

logger = setup_logger(__name__)


def _safe_float(value: str | float | None) -> Optional[float]:
    """Convert FRED observation values to float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = value.strip()
    if not value or value == ".":
        return None
    try:
        return float(value)
    except ValueError:
        return None


INDICATOR_CONFIG: Dict[str, Dict[str, object]] = {
    "CPI": {
        "series_id": "CPIAUCSL",
        "name": "美国CPI(城市居民消费价格指数,季调)",
        "unit": "index",
        "frequency": "monthly",
        "lookback": 14,
        "description": "衡量美国城市居民消费品与服务价格的平均变动",
        "trigger_rules": {
            "mom_pct_threshold": 0.3,  # 月环比变化超过 0.3%
            "yoy_pct_threshold": 0.5,  # 年同比变化超过 0.5%
            "surprise_pct_threshold": 0.2,  # 与市场预期偏差超过 0.2%
        },
    },
    "CORE_CPI": {
        "series_id": "CPILFESL",
        "name": "美国核心CPI(剔除食品与能源)",
        "unit": "index",
        "frequency": "monthly",
        "lookback": 14,
        "description": "剔除食品及能源价格的核心 CPI",
        "trigger_rules": {
            "mom_pct_threshold": 0.3,
            "yoy_pct_threshold": 0.5,
        },
    },
    "FED_FUNDS": {
        "series_id": "FEDFUNDS",
        "name": "联邦基金目标利率(上限)",
        "unit": "percent",
        "frequency": "monthly",
        "lookback": 14,
        "description": "美国联邦基金有效利率",
        "trigger_rules": {
            "absolute_change_threshold": 0.25,  # 25 bp 以上
        },
    },
    "UNEMPLOYMENT": {
        "series_id": "UNRATE",
        "name": "美国失业率",
        "unit": "percent",
        "frequency": "monthly",
        "lookback": 20,
        "description": "美国劳工统计局公布的失业率",
        "trigger_rules": {
            "absolute_change_threshold": 0.3,
            "yoy_pct_threshold": 0.5,
        },
    },
    "DXY": {
        "series_id": "DTWEXBGS",
        "name": "贸易权重美元指数",
        "unit": "index",
        "frequency": "daily",
        "lookback": 90,
        "description": "贸易加权美元指数 (DTWEXBGS)",
        "trigger_rules": {
            "level_threshold": 105.0,
            "deviation_from_ma_pct": 1.0,  # 相对30日均线偏离超过1%
        },
    },
    "VIX": {
        "series_id": "VIXCLS",
        "name": "CBOE VIX 波动率指数",
        "unit": "index",
        "frequency": "daily",
        "lookback": 90,
        "description": "芝加哥期权交易所波动率指数",
        "trigger_rules": {
            "level_threshold": 25.0,
            "deviation_from_ma_pct": 15.0,
        },
    },
}


class FREDMacroProvider(MacroProvider):
    """Fetch macro indicators using FRED API."""

    API_BASE_URL = "https://api.stlouisfed.org/fred"
    SERIES_OBSERVATIONS_ENDPOINT = "/series/observations"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._api_key = getattr(config, "FRED_API_KEY", "").strip()
        self._base_url = getattr(config, "FRED_API_BASE_URL", self.API_BASE_URL).rstrip("/")
        self._indicator_config = INDICATOR_CONFIG
        expectations = getattr(config, "MACRO_EXPECTATIONS", {}) or {}
        self._expectations = expectations if isinstance(expectations, dict) else {}

        if not self._api_key:
            logger.warning("FRED API Key 未配置，可能会触发请求限流")

    async def snapshot(self, *, indicator: str) -> ToolResult:
        indicator_key = indicator.strip().upper()
        cfg = self._indicator_config.get(indicator_key)

        if not indicator_key:
            return ToolResult(
                source="FRED",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="indicator_required",
            )

        if not cfg:
            return ToolResult(
                source="FRED",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="indicator_not_supported",
            )

        series_id = cfg["series_id"]  # type: ignore[assignment]
        lookback = int(cfg.get("lookback", 24))
        observations, error = await self._fetch_observations(series_id, lookback)

        if error:
            return ToolResult(
                source="FRED",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=error,
            )

        metrics, anomalies = self._compute_metrics(indicator_key, cfg, observations)

        data = {
            "indicator": indicator_key,
            "indicator_name": cfg.get("name"),
            "series_id": series_id,
            "frequency": cfg.get("frequency", "unknown"),
            "unit": cfg.get("unit", "unknown"),
            "release_time": metrics.get("release_time"),
            "metrics": metrics,
            "anomalies": anomalies,
            "thresholds": cfg.get("trigger_rules", {}),
            "source": "FRED",
            "notes": cfg.get("description", ""),
        }

        triggered = any(anomalies.values())
        confidence = 1.0 if observations else 0.0

        return ToolResult(
            source="FRED",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data=data,
            triggered=triggered,
            confidence=confidence,
        )

    async def _fetch_observations(
        self,
        series_id: str,
        lookback: int,
    ) -> Tuple[List[dict], Optional[str]]:
        params = {
            "series_id": series_id,
            "limit": lookback,
            "sort_order": "desc",
            "file_type": "json",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        url = f"{self._base_url}{self.SERIES_OBSERVATIONS_ENDPOINT}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                if response.status_code == 429:
                    raise ToolRateLimitError("FRED API rate limited")
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            logger.warning("FRED 请求超时: series=%s", series_id)
            return [], f"timeout: {exc}"
        except ToolRateLimitError:
            return [], "rate_limit"
        except Exception as exc:
            logger.error("FRED 请求失败: series=%s error=%s", series_id, exc)
            return [], str(exc)

        observations = payload.get("observations", []) if isinstance(payload, dict) else []

        # 过滤缺失或非数值数据
        filtered = []
        for obs in observations:
            value = _safe_float(obs.get("value"))
            if value is None:
                continue
            filtered.append({"date": obs.get("date"), "value": value})

        return filtered, None

    def _compute_metrics(
        self,
        indicator_key: str,
        cfg: Dict[str, object],
        observations: List[dict],
    ) -> Tuple[Dict[str, object], Dict[str, bool]]:
        if not observations:
            return {}, {}

        latest = observations[0]
        previous = observations[1] if len(observations) > 1 else None
        year_ago = observations[12] if len(observations) > 12 else None

        value = _safe_float(latest.get("value"))
        previous_value = _safe_float(previous.get("value") if previous else None)
        year_ago_value = _safe_float(year_ago.get("value") if year_ago else None)

        change_abs = value - previous_value if (value is not None and previous_value is not None) else None
        change_mom_pct = (
            (value - previous_value) / previous_value * 100
            if value is not None and previous_value not in {None, 0}
            else None
        )
        change_yoy_pct = (
            (value - year_ago_value) / year_ago_value * 100
            if value is not None and year_ago_value not in {None, 0}
            else None
        )

        rolling_window = 30 if cfg.get("frequency") == "daily" else 6
        window_values = [
            obs["value"] for obs in observations[:rolling_window] if _safe_float(obs.get("value")) is not None
        ]
        moving_average = statistics.mean(window_values) if window_values else None
        deviation_from_ma_pct = (
            (value - moving_average) / moving_average * 100
            if value is not None and moving_average not in {None, 0}
            else None
        )

        release_time = None
        if latest.get("date"):
            try:
                release_time = datetime.fromisoformat(latest["date"]).isoformat()
            except ValueError:
                release_time = latest["date"]

        metrics: Dict[str, object] = {
            "value": value,
            "previous": previous_value,
            "year_ago": year_ago_value,
            "change_abs": change_abs,
            "change_mom_pct": change_mom_pct,
            "change_yoy_pct": change_yoy_pct,
            "moving_average": moving_average,
            "deviation_from_ma_pct": deviation_from_ma_pct,
            "release_time": release_time,
        }

        expectation_value = None
        surprise = None
        surprise_pct = None
        if indicator_key in self._expectations and value is not None:
            try:
                expectation_value = float(self._expectations[indicator_key])
                surprise = value - expectation_value
                surprise_pct = (
                    (value - expectation_value) / expectation_value * 100
                    if expectation_value not in {None, 0}
                    else None
                )
            except (TypeError, ValueError):
                expectation_value = None
                surprise = None
                surprise_pct = None

        metrics["expectation"] = expectation_value
        metrics["surprise"] = surprise
        metrics["surprise_pct"] = surprise_pct

        anomalies = self._evaluate_triggers(cfg.get("trigger_rules", {}), metrics)
        return metrics, anomalies

    def _evaluate_triggers(
        self,
        rules: Dict[str, float] | object,
        metrics: Dict[str, object],
    ) -> Dict[str, bool]:
        """Evaluate anomaly rules based on configured thresholds."""
        if not isinstance(rules, dict) or not rules:
            return {}

        anomalies: Dict[str, bool] = {}

        mom_threshold = rules.get("mom_pct_threshold")
        if mom_threshold is not None:
            value = metrics.get("change_mom_pct")
            anomalies["mom_spike"] = (
                value is not None and abs(float(value)) >= float(mom_threshold)
            )

        yoy_threshold = rules.get("yoy_pct_threshold")
        if yoy_threshold is not None:
            value = metrics.get("change_yoy_pct")
            anomalies["yoy_spike"] = (
                value is not None and abs(float(value)) >= float(yoy_threshold)
            )

        abs_threshold = rules.get("absolute_change_threshold")
        if abs_threshold is not None:
            value = metrics.get("change_abs")
            anomalies["absolute_jump"] = (
                value is not None and abs(float(value)) >= float(abs_threshold)
            )

        level_threshold = rules.get("level_threshold")
        if level_threshold is not None:
            value = metrics.get("value")
            anomalies["level_extreme"] = (
                value is not None and float(value) >= float(level_threshold)
            )

        deviation_threshold = rules.get("deviation_from_ma_pct")
        if deviation_threshold is not None:
            value = metrics.get("deviation_from_ma_pct")
            anomalies["moving_average_deviation"] = (
                value is not None and abs(float(value)) >= float(deviation_threshold)
            )

        surprise_threshold = rules.get("surprise_pct_threshold")
        if surprise_threshold is not None:
            value = metrics.get("surprise_pct")
            anomalies["consensus_surprise"] = (
                value is not None and abs(float(value)) >= float(surprise_threshold)
            )

        # remove empty anomalies (False) to keep ToolResult concise
        anomalies = {key: val for key, val in anomalies.items() if val}
        return anomalies
