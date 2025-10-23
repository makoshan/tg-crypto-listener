#!/bin/bash
# Test script for Codex CLI tools

set -e

echo "=========================================="
echo "Testing Codex CLI Tools"
echo "=========================================="
echo ""

# Test 1: Search News Tool
echo "Test 1: Search News Tool"
echo "Command: uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py --query 'Bitcoin ETF approval' --max-results 3"
echo ""

uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "Bitcoin ETF approval" \
    --max-results 3 2>&1 | head -30

echo ""
echo "✅ Search news tool test completed"
echo ""

# Test 2: Fetch Price Tool (Single Asset)
echo "=========================================="
echo "Test 2: Fetch Price Tool (Single Asset)"
echo "Command: uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py --assets BTC"
echo ""

uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
    --assets BTC 2>&1 | head -30

echo ""
echo "✅ Fetch price tool (single) test completed"
echo ""

# Test 3: Fetch Price Tool (Multiple Assets)
echo "=========================================="
echo "Test 3: Fetch Price Tool (Multiple Assets)"
echo "Command: uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py --assets BTC ETH SOL"
echo ""

uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
    --assets BTC ETH SOL 2>&1 | head -50

echo ""
echo "✅ Fetch price tool (multiple) test completed"
echo ""

# Test 4: Fetch Memory Tool
echo "=========================================="
echo "Test 4: Fetch Memory Tool"
echo "Command: uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py --query 'Bitcoin price' --asset BTC --limit 2"
echo ""

uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "Bitcoin price" \
    --asset BTC \
    --limit 2 2>&1 | head -30

echo ""
echo "✅ Fetch memory tool test completed"
echo ""

echo "=========================================="
echo "All tests completed!"
echo "=========================================="
