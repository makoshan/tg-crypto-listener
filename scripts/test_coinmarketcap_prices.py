#!/usr/bin/env python3
"""
CoinMarketCap 价格提供器诊断脚本。

手动运行示例:
    poetry run python scripts/test_coinmarketcap_prices.py --disable-binance btc eth sol
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Iterable, List

from src.ai.tools.price.providers.coinmarketcap import (
    CoinMarketCapAssetRegistry,
    CoinMarketCapPriceProvider,
)
from src.config import Config, PROJECT_ROOT


DEFAULT_ASSETS: List[str] = ["btc", "eth", "sol", "usde", "ena", "brett"]


@dataclass
class DiagnosticEntry:
    symbol: str
    status: str
    message: str
    price_usd: float | None
    confidence: float
    triggered: bool
    raw: dict


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="测试 CoinMarketCap 价格请求，帮助定位 API Key 或资产解析问题。",
    )
    parser.add_argument(
        "assets",
        nargs="*",
        default=DEFAULT_ASSETS,
        help=f"待检测的资产符号（默认: {', '.join(DEFAULT_ASSETS)}）",
    )
    parser.add_argument(
        "--disable-binance",
        action="store_true",
        help="关闭 Binance 降级，以直接观察 CoinMarketCap 的原始错误。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出诊断结果，便于后续分析。",
    )
    return parser.parse_args(list(argv))


async def _diagnose_assets(
    *,
    assets: Iterable[str],
    disable_binance: bool,
) -> tuple[list[DiagnosticEntry], bool]:
    config = Config()
    if disable_binance:
        setattr(config, "PRICE_BINANCE_FALLBACK_ENABLED", False)

    provider = CoinMarketCapPriceProvider(config)
    has_api_key = bool(getattr(provider, "_api_key", ""))
    results: list[DiagnosticEntry] = []

    for symbol in assets:
        try:
            result = await provider.snapshot(asset=symbol)
        except Exception as exc:  # pragma: no cover - 防御性处理
            entry = DiagnosticEntry(
                symbol=symbol.upper(),
                status="FAIL",
                message=f"异常: {exc}",
                price_usd=None,
                confidence=0.0,
                triggered=False,
                raw={},
            )
            results.append(entry)
            continue

        price = None
        if result.data.get("metrics"):
            price = result.data["metrics"].get("price_usd")

        if not result.success:
            status = "FAIL"
            message = result.error or "请求失败"
        else:
            notes = (result.data.get("notes") or "").strip()
            cmc_id = result.data.get("coinmarketcap_id")
            if cmc_id is None or "Binance 行情降级" in notes:
                status = "WARN"
                message = notes or "使用 Binance 行情降级"
            else:
                status = "OK"
                message = notes or "CoinMarketCap 返回成功"

        entry = DiagnosticEntry(
            symbol=symbol.upper(),
            status=status,
            message=message,
            price_usd=price,
            confidence=result.confidence,
            triggered=result.triggered,
            raw={
                "success": result.success,
                "error": result.error,
                "data": result.data,
            },
        )
        results.append(entry)

    return results, has_api_key


def _print_human_readable(entries: list[DiagnosticEntry], *, has_api_key: bool) -> None:
    print("\nCoinMarketCap 价格诊断\n" + "=" * 32)

    cache_path = CoinMarketCapAssetRegistry.CACHE_PATH
    if cache_path.exists():
        print(f"🗂️  本地缓存: {cache_path}")
    else:
        print("🗂️  本地缓存: 未生成 (.cache/coinmarketcap_ids.json)")

    api_key_hint = "已配置" if has_api_key else "未配置"
    print(f"🔑 API Key 状态: {api_key_hint}")
    print()

    for entry in entries:
        header = f"[{entry.status}] {entry.symbol}"
        print(header)
        if entry.price_usd is not None:
            print(f"  • 价格: ${entry.price_usd}")
        print(f"  • 信息: {entry.message}")
        if entry.status != "OK":
            error_hint = entry.raw.get("error")
            if error_hint:
                print(f"  • 错误: {error_hint}")
        print(f"  • 置信度: {entry.confidence} (triggered={entry.triggered})")
        print()


def _print_json(entries: list[DiagnosticEntry]) -> None:
    payload = [
        {
            "symbol": entry.symbol,
            "status": entry.status,
            "message": entry.message,
            "price_usd": entry.price_usd,
            "confidence": entry.confidence,
            "triggered": entry.triggered,
            "raw": entry.raw,
        }
        for entry in entries
    ]
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    print()


def main(argv: Iterable[str]) -> int:
    args = _parse_args(argv)
    entries, has_api_key = asyncio.run(
        _diagnose_assets(
            assets=args.assets,
            disable_binance=args.disable_binance,
        )
    )

    if args.json:
        _print_json(entries)
    else:
        _print_human_readable(entries, has_api_key=has_api_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
