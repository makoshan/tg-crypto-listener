"""Test AnthropicClient Memory Tool API compliance.

This test verifies that the AnthropicClient correctly implements
the Anthropic Memory Tool API (memory_20250818) as documented at:
https://docs.claude.com/en/docs/agents-and-tools/tool-use/memory-tool
"""

import pytest


def test_memory_tool_definition_format():
    """Verify Memory Tool uses correct type and name."""
    from src.ai.anthropic_client import AnthropicClient
    from src.memory.memory_tool_handler import MemoryToolHandler

    # Create a minimal client with memory handler
    memory_handler = MemoryToolHandler(base_path="/tmp/test_memories")
    client = AnthropicClient(
        api_key="test-key",
        model_name="claude-sonnet-4-5-20250929",
        memory_handler=memory_handler,
    )

    # Get tool definitions
    tools = client._build_tool_definitions()

    # Verify tool exists
    assert tools is not None, "Tools should not be None when memory_handler is provided"
    assert len(tools) == 1, "Should have exactly one tool (Memory Tool)"

    tool = tools[0]

    # Critical: Must use memory_20250818 type, not custom tool
    assert tool.get("type") == "memory_20250818", \
        "Tool type must be 'memory_20250818' per Anthropic docs"

    # Critical: Must use standard name 'memory'
    assert tool.get("name") == "memory", \
        "Tool name must be 'memory' per Anthropic docs"

    # Should NOT have input_schema (that's handled by Anthropic)
    assert "input_schema" not in tool, \
        "memory_20250818 tools should not have input_schema"

    # Should NOT have description (that's handled by Anthropic)
    assert "description" not in tool, \
        "memory_20250818 tools should not have description"


def test_no_memory_handler_returns_none():
    """Verify no tools returned when memory_handler is None."""
    from src.ai.anthropic_client import AnthropicClient

    client = AnthropicClient(
        api_key="test-key",
        model_name="claude-sonnet-4-5-20250929",
        memory_handler=None,
    )

    tools = client._build_tool_definitions()
    assert tools is None, "Should return None when memory_handler is None"


def test_beta_header_configuration():
    """Verify beta header is configured correctly."""
    from src.ai.anthropic_client import AnthropicClient
    from src.memory.memory_tool_handler import MemoryToolHandler

    memory_handler = MemoryToolHandler(base_path="/tmp/test_memories")
    client = AnthropicClient(
        api_key="test-key",
        model_name="claude-sonnet-4-5-20250929",
        memory_handler=memory_handler,
    )

    # Verify beta header is configured
    assert client._betas == ["context-management-2025-06-27"], \
        "Beta header must include context-management-2025-06-27"


def test_tool_execution_checks_correct_name():
    """Verify _execute_tool checks for 'memory' not 'memory_tool'."""
    from src.ai.anthropic_client import AnthropicClient
    from src.memory.memory_tool_handler import MemoryToolHandler

    memory_handler = MemoryToolHandler(base_path="/tmp/test_memories")
    client = AnthropicClient(
        api_key="test-key",
        model_name="claude-sonnet-4-5-20250929",
        memory_handler=memory_handler,
    )

    # Test with correct name 'memory'
    tool_block = {
        "name": "memory",
        "input": {
            "command": "view",
            "path": "test.md"
        }
    }
    result = client._execute_tool(tool_block)
    # Should attempt execution (may fail due to missing file, but won't reject tool name)
    assert "Unsupported tool" not in result.get("error", ""), \
        "Should accept tool name 'memory'"

    # Test with old incorrect name 'memory_tool'
    tool_block_old = {
        "name": "memory_tool",
        "input": {
            "command": "view",
            "path": "test.md"
        }
    }
    result_old = client._execute_tool(tool_block_old)
    assert result_old.get("success") is False, \
        "Should reject old tool name"
    assert "Unsupported tool: memory_tool" in result_old.get("error", ""), \
        "Should return unsupported tool error for old name"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
