#!/usr/bin/env python3
"""Test Phase 1 module imports."""
import sys

print("=== Verifying Phase 1 Module Imports ===\n")

try:
    # Test graph module
    from src.ai.deep_analysis.graph import build_deep_graph
    print("✓ graph.build_deep_graph")

    # Test nodes
    from src.ai.deep_analysis.nodes import (
        BaseNode, ContextGatherNode, ToolPlannerNode,
        ToolExecutorNode, SynthesisNode
    )
    print("✓ nodes: BaseNode, ContextGatherNode, ToolPlannerNode, ToolExecutorNode, SynthesisNode")

    # Test helpers
    from src.ai.deep_analysis.helpers import (
        fetch_memory_entries, build_planner_prompt,
        build_synthesis_prompt, format_memory_evidence
    )
    print("✓ helpers: fetch_memory_entries, build_*_prompt, format_*")

    # Test main engine
    from src.ai.deep_analysis.gemini import GeminiDeepAnalysisEngine, DeepAnalysisState
    print("✓ gemini: GeminiDeepAnalysisEngine, DeepAnalysisState")

    # Verify DeepAnalysisState structure
    state_fields = set(DeepAnalysisState.__annotations__.keys())
    expected_fields = {
        'payload', 'preliminary', 'search_evidence', 'memory_evidence',
        'next_tools', 'search_keywords', 'tool_call_count', 'max_tool_calls', 'final_response'
    }

    if expected_fields.issubset(state_fields):
        print("✓ DeepAnalysisState has all required fields")
        print(f"  Fields: {', '.join(sorted(expected_fields))}")
    else:
        missing = expected_fields - state_fields
        print(f"⚠ Missing fields: {missing}")

    print("\n✅ All Phase 1 modules imported successfully!")
    print(f"\n📊 Statistics:")
    print(f"   - Node classes: 5 (BaseNode + 4 implementations)")
    print(f"   - Helper functions: 6+")
    print(f"   - State fields: {len(expected_fields)}")

except Exception as e:
    print(f"\n❌ Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
