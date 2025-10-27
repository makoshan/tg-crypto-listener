import pytest

pytest.importorskip("openai")

from unittest.mock import Mock

from src.ai.deep_analysis.factory import create_deep_analysis_engine
from src.config import Config


@pytest.fixture
def base_config():
    config = Mock(spec=Config)

    config.DASHSCOPE_API_KEY = "sk-test"
    config.QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    config.QWEN_DEEP_TIMEOUT_SECONDS = 30.0
    config.QWEN_DEEP_MAX_FUNCTION_TURNS = 6
    config.QWEN_ENABLE_SEARCH = False

    config.TOOL_SEARCH_ENABLED = False
    config.TOOL_PRICE_ENABLED = False
    config.TOOL_MACRO_ENABLED = False
    config.TOOL_ONCHAIN_ENABLED = False
    config.TOOL_PROTOCOL_ENABLED = False

    return config


def _make_config(config: Mock, model: str):
    def _deep_config():
        return {
            "enabled": True,
            "provider": "qwen",
            "qwen": {
                "api_key": config.DASHSCOPE_API_KEY,
                "base_url": config.QWEN_BASE_URL,
                "model": model,
                "timeout": config.QWEN_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": config.QWEN_DEEP_MAX_FUNCTION_TURNS,
                "enable_search": config.QWEN_ENABLE_SEARCH,
            },
        }

    config.get_deep_analysis_config = _deep_config
    return config


def _dummy_parser(raw: str):
    return raw


def test_qwen_model_alias_is_normalised(base_config):
    config = _make_config(base_config, "Qwen/Qwen3-Max-Instruct")

    engine = create_deep_analysis_engine(
        provider="qwen",
        config=config,
        parse_callback=_dummy_parser,
        memory_bundle=None,
    )

    assert engine.model == "qwen3-max"


def test_qwen_model_without_alias_is_unchanged(base_config):
    config = _make_config(base_config, "qwen-plus")

    engine = create_deep_analysis_engine(
        provider="qwen",
        config=config,
        parse_callback=_dummy_parser,
        memory_bundle=None,
    )

    assert engine.model == "qwen-plus"
