import asyncio
import json

import pytest

from src.ai.deep_analysis.base import build_deep_analysis_messages


class DummyPayload:
    text = "hello"
    translated_text = None
    source = "test"
    from datetime import datetime
    import datetime as _dt
    timestamp = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc)
    historical_reference = {"entries": []}
    keywords_hit = []


class DummyPreliminary:
    summary = "s"
    event_type = "whale"
    asset = "BTC"
    action = "observe"
    confidence = 0.5
    risk_flags = []
    notes = ""
    links = []


def test_build_messages_injects_memory_evidence():
    payload = DummyPayload()
    prelim = DummyPreliminary()

    memory_evidence = {"supabase_hits": [{"news_event_id": 1}], "notes": "ok"}
    messages = build_deep_analysis_messages(
        payload,
        prelim,
        additional_context={
            "analysis_capabilities": {"provider": "test", "tool_enabled": False},
            "memory_evidence": memory_evidence,
        },
    )

    assert isinstance(messages, list) and len(messages) == 2
    user_msg = messages[1]["content"]
    assert "memory_evidence" in user_msg
    assert "supabase_hits" in user_msg


