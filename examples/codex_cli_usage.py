"""Codex CLI 使用示例

演示如何使用 codex exec 进行工具规划和信号综合。
"""

import asyncio
import json
import re
from typing import List, Dict, Any


class CodexCliPlanner:
    """使用 codex exec 的规划器实现."""

    def __init__(self, timeout: int = 60, max_tokens: int = 4000):
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def plan(self, state: Dict[str, Any], available_tools: List[str]) -> Dict[str, Any]:
        """
        使用 codex exec 决策工具调用.

        示例：
            state = {
                'payload': {'text': 'BTC ETF 获批', ...},
                'preliminary': {'event_type': 'listing', 'asset': 'BTC', ...},
                ...
            }
            available_tools = ['search', 'price', 'macro']

            result = await planner.plan(state, available_tools)
            # => {'tools': ['search', 'price'], 'search_keywords': '...', ...}
        """
        # 1. 构建 prompt
        prompt = self._build_planning_prompt(state, available_tools)

        # 2. 调用 codex exec（引用文档作为上下文）
        cli_output = await self._invoke_codex_exec(
            prompt, context_file="@docs/codex_cli_integration_plan.md"
        )

        # 3. 解析输出
        try:
            json_text = self._extract_json(cli_output)
            result = json.loads(json_text)

            # 验证必需字段
            assert "tools" in result, "Missing 'tools' field"
            assert isinstance(result["tools"], list), "'tools' must be a list"

            return {
                "tools": result.get("tools", []),
                "search_keywords": result.get("search_keywords", ""),
                "macro_indicators": result.get("macro_indicators", []) or [],
                "onchain_assets": result.get("onchain_assets", []) or [],
                "protocol_slugs": result.get("protocol_slugs", []) or [],
                "reason": result.get("reason", ""),
            }

        except json.JSONDecodeError as exc:
            print(f"❌ JSON 解析失败: {exc}")
            print(f"原始输出: {cli_output[:500]}")
            return {"tools": [], "reason": "JSON parse error"}

    async def synthesize(self, state: Dict[str, Any]) -> str:
        """
        使用 codex exec 综合证据生成最终信号.

        示例：
            state = {
                'payload': {...},
                'preliminary': {...},
                'search_evidence': {...},
                'price_evidence': {...},
                ...
            }

            result_json = await planner.synthesize(state)
            # => '{"summary": "...", "event_type": "...", "confidence": 0.8, ...}'
        """
        prompt = self._build_synthesis_prompt(state)

        cli_output = await self._invoke_codex_exec(
            prompt, context_file="@docs/codex_cli_integration_plan.md"
        )

        # 提取并验证 JSON
        json_text = self._extract_json(cli_output)
        json.loads(json_text)  # 验证格式
        return json_text

    async def _invoke_codex_exec(
        self, prompt: str, context_file: str = None
    ) -> str:
        """
        调用 codex exec 命令.

        Args:
            prompt: 输入提示词
            context_file: 可选的上下文文件（如 @docs/xxx.md）

        Returns:
            CLI 标准输出

        Raises:
            TimeoutError: CLI 超时
            RuntimeError: CLI 执行失败
        """
        # 构建完整 prompt（包含上下文引用）
        full_prompt = prompt
        if context_file:
            full_prompt = f"{prompt}\n\n{context_file}"

        # 调用 codex exec
        proc = await asyncio.create_subprocess_exec(
            "codex",
            "exec",
            full_prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Codex CLI timeout after {self.timeout}s")

        if proc.returncode != 0:
            error_msg = stderr.decode()
            raise RuntimeError(f"Codex CLI failed (code {proc.returncode}): {error_msg}")

        return stdout.decode().strip()

    def _build_planning_prompt(
        self, state: Dict[str, Any], available_tools: List[str]
    ) -> str:
        """构建规划 prompt."""
        payload = state.get("payload", {})
        preliminary = state.get("preliminary", {})

        # 收集已有证据摘要
        evidence_summary = self._summarize_evidence(state)

        prompt = f"""你是加密交易台的资深分析师，负责决定下一步需要调用哪些工具来增强分析质量。

## 当前事件
文本: {payload.get('text', '')}
翻译: {payload.get('translated_text', '')}
来源: {payload.get('source', '')}

## 初步分析
类型: {preliminary.get('event_type', '')}
资产: {preliminary.get('asset', '')}
置信度: {preliminary.get('confidence', 0.0)}
摘要: {preliminary.get('summary', '')}

## 已收集证据
{evidence_summary}

## 可用工具
{self._describe_tools(available_tools)}

## 任务
决定下一步需要调用哪些工具来增强分析质量。如果已有足够证据，返回空工具列表。

## 输出格式（必须是有效的 JSON）
{{
  "tools": ["tool1", "tool2"],
  "search_keywords": "关键词（仅当 tools 包含 search）",
  "macro_indicators": ["CPI"]（仅当 tools 包含 macro）,
  "onchain_assets": ["USDC"]（仅当 tools 包含 onchain）,
  "protocol_slugs": ["aave"]（仅当 tools 包含 protocol）,
  "reason": "决策理由"
}}

请直接输出 JSON，不要包含 markdown 代码块标记。
"""
        return prompt

    def _build_synthesis_prompt(self, state: Dict[str, Any]) -> str:
        """构建综合 prompt."""
        payload = state.get("payload", {})
        preliminary = state.get("preliminary", {})

        # 收集所有证据
        evidence = {
            "search": state.get("search_evidence"),
            "price": state.get("price_evidence"),
            "macro": state.get("macro_evidence"),
            "onchain": state.get("onchain_evidence"),
            "protocol": state.get("protocol_evidence"),
        }
        evidence = {k: v for k, v in evidence.items() if v is not None}

        prompt = f"""你是加密交易台的资深分析师，现在需要综合所有证据生成最终交易信号。

## 原始事件
文本: {payload.get('text', '')}
翻译: {payload.get('translated_text', '')}
来源: {payload.get('source', '')}

## 初步分析
{json.dumps(preliminary, ensure_ascii=False, indent=2)}

## 收集的证据
{json.dumps(evidence, ensure_ascii=False, indent=2)}

## 任务
综合以上所有信息，生成最终的交易信号分析。

## 输出格式（必须是有效的 JSON）
{{
  "summary": "中文摘要",
  "event_type": "事件类型（listing/hack/regulation/...）",
  "asset": "资产代码",
  "action": "buy/sell/observe",
  "confidence": 0.0-1.0,
  "risk_flags": ["标志1", "标志2"],
  "notes": "分析笔记",
  "links": ["链接1", "链接2"]
}}

**关键要求**：
- 必须输出有效的 JSON 格式
- confidence 表示该信号作为交易建议的可靠性（非事件真实性）
- 不要包含 markdown 代码块标记

请直接输出 JSON。
"""
        return prompt

    def _summarize_evidence(self, state: Dict[str, Any]) -> str:
        """总结已收集的证据."""
        parts = []
        if state.get("search_evidence"):
            parts.append("✅ 搜索证据已收集")
        if state.get("price_evidence"):
            parts.append("✅ 价格数据已收集")
        if state.get("macro_evidence"):
            parts.append("✅ 宏观数据已收集")
        if state.get("onchain_evidence"):
            parts.append("✅ 链上数据已收集")
        if state.get("protocol_evidence"):
            parts.append("✅ 协议数据已收集")

        return "\n".join(parts) if parts else "无已收集证据"

    def _describe_tools(self, available_tools: List[str]) -> str:
        """生成工具描述."""
        descriptions = {
            "search": "搜索工具 - 在权威加密新闻网站搜索相关信息",
            "price": "价格工具 - 获取资产的实时价格和市场数据",
            "macro": "宏观工具 - 查询宏观经济指标（CPI、利率等）",
            "onchain": "链上工具 - 查询稳定币流动性和链上数据",
            "protocol": "协议工具 - 查询 DeFi 协议的 TVL 和健康度",
        }

        lines = []
        for tool in available_tools:
            desc = descriptions.get(tool, f"{tool} 工具")
            lines.append(f"- {tool}: {desc}")

        return "\n".join(lines)

    def _extract_json(self, text: str) -> str:
        """
        从文本中提取 JSON（支持 markdown 包裹）.

        支持的格式：
        1. ```json ... ```
        2. ``` ... ```
        3. 纯 JSON
        """
        # 尝试 markdown json 代码块
        if "```json" in text:
            match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
            if match:
                return match.group(1)

        # 尝试通用代码块
        if "```" in text:
            match = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
            if match:
                return match.group(1)

        # 假设整个文本是 JSON
        return text.strip()


# ==================== 使用示例 ====================


async def example_planning():
    """示例：使用 codex exec 进行工具规划."""
    print("=== 示例 1: 工具规划 ===\n")

    planner = CodexCliPlanner(timeout=60)

    # 构造模拟状态
    state = {
        "payload": {
            "text": "BTC ETF 获 SEC 批准",
            "translated_text": "BTC ETF approved by SEC",
            "source": "CoinDesk",
        },
        "preliminary": {
            "event_type": "listing",
            "asset": "BTC",
            "confidence": 0.8,
            "summary": "BTC ETF 批准消息",
        },
        "search_evidence": None,
        "price_evidence": None,
    }

    available_tools = ["search", "price", "macro", "onchain", "protocol"]

    try:
        result = await planner.plan(state, available_tools)
        print(f"✅ 规划结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"❌ 规划失败: {exc}")


async def example_synthesis():
    """示例：使用 codex exec 综合证据."""
    print("\n=== 示例 2: 证据综合 ===\n")

    planner = CodexCliPlanner(timeout=60)

    # 构造模拟状态（包含证据）
    state = {
        "payload": {
            "text": "BTC ETF 获 SEC 批准",
            "translated_text": "BTC ETF approved by SEC",
            "source": "CoinDesk",
        },
        "preliminary": {
            "event_type": "listing",
            "asset": "BTC",
            "confidence": 0.8,
        },
        "search_evidence": {
            "success": True,
            "data": {
                "results": [
                    {"title": "SEC Approves First Bitcoin ETF", "source": "CoinDesk"}
                ]
            },
        },
        "price_evidence": {
            "success": True,
            "data": {"price_usd": 45000, "change_24h": 5.2},
        },
    }

    try:
        result_json = await planner.synthesize(state)
        result = json.loads(result_json)
        print(f"✅ 综合结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"❌ 综合失败: {exc}")


async def example_error_handling():
    """示例：错误处理."""
    print("\n=== 示例 3: 错误处理 ===\n")

    planner = CodexCliPlanner(timeout=5)  # 短超时

    state = {
        "payload": {"text": "测试消息"},
        "preliminary": {"event_type": "other", "asset": "NONE"},
    }

    try:
        result = await planner.plan(state, ["search"])
        print(f"✅ 成功: {result}")
    except TimeoutError as exc:
        print(f"⏱️ 超时: {exc}")
    except RuntimeError as exc:
        print(f"❌ CLI 失败: {exc}")
    except Exception as exc:
        print(f"❌ 其他错误: {exc}")


async def main():
    """运行所有示例."""
    print("Codex CLI Planner 使用示例\n")
    print("=" * 60)

    # 示例 1: 工具规划
    await example_planning()

    # 示例 2: 证据综合
    await example_synthesis()

    # 示例 3: 错误处理
    await example_error_handling()

    print("\n" + "=" * 60)
    print("示例运行完成！")


if __name__ == "__main__":
    asyncio.run(main())
