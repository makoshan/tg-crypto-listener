"""Tests for SOURCE_CHANNELS parsing."""

from src.config import _parse_source_channels


def test_parse_source_channels_filters_trailing_backslash():
    value = "@lookonchainchannel,@OnchainLens,@WalterBloomberg,\\"
    assert _parse_source_channels(value) == [
        "@lookonchainchannel",
        "@OnchainLens",
        "@WalterBloomberg",
    ]


def test_parse_source_channels_handles_newlines_and_comments():
    value = (
        "@alpha,@beta\\\n"
        "# comment that should be ignored\n"
        " @gamma , '@delta' , \"@epsilon\" , \\\n"
        "\n"
    )
    assert _parse_source_channels(value) == [
        "@alpha",
        "@beta",
        "@gamma",
        "@delta",
        "@epsilon",
    ]
