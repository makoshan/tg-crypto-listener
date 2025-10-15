"""Typed payloads used by the repository layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class NewsEventPayload:
    source: str
    source_message_id: str
    published_at: datetime
    content_text: str
    language: str
    hash_raw: str
    ingest_status: str
    summary: Optional[str] = None
    translated_text: Optional[str] = None
    source_url: Optional[str] = None
    hash_canonical: Optional[str] = None
    keywords_hit: List[str] = field(default_factory=list)
    media_refs: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    price_snapshot: Optional[Dict[str, Any]] = None


@dataclass
class AiSignalPayload:
    news_event_id: int
    model_name: str
    summary_cn: str
    event_type: str
    assets: str
    action: str
    direction: str
    confidence: float
    strength: str
    risk_flags: List[str]
    notes: Optional[str]
    links: List[str]
    execution_path: str
    should_alert: bool
    latency_ms: Optional[int] = None
    asset_names: Optional[str] = None
    raw_response: Optional[str] = None
    price_snapshot: Optional[Dict[str, Any]] = None


@dataclass
class StrategyInsightPayload:
    title: str
    narrative: Optional[str] = None
    action: Optional[str] = None
    confidence: Optional[float] = None
    source_urls: List[str] = field(default_factory=list)
    news_event_ids: List[int] = field(default_factory=list)
    ai_signal_ids: List[int] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    summary: Optional[str] = None
    relation: Optional[str] = None
