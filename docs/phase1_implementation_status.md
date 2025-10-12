# Phase 1 搜索工具集成 - 实施状态

**日期**: 2025-10-11
**状态**: 基础架构已完成，LangGraph 集成待实施

---

## ✅ 已完成部分

### 1. 工具基础架构 (100%)

#### 目录结构
```
src/ai/tools/
├── __init__.py           ✅ 完成
├── base.py               ✅ 完成 - ToolResult, BaseTool
├── exceptions.py         ✅ 完成 - ToolFetchError, ToolRateLimitError, ToolTimeoutError
└── search/
    ├── __init__.py       ✅ 完成 - Provider registry
    ├── fetcher.py        ✅ 完成 - SearchTool with caching
    └── providers/
        ├── __init__.py   ✅ 完成
        ├── base.py       ✅ 完成 - SearchProvider abstract base
        └── tavily.py     ✅ 完成 - TavilySearchProvider implementation
```

#### 关键特性
- ✅ **ToolResult** 标准化结果格式（所有工具复用）
- ✅ **BaseTool** 抽象基类，统一超时处理
- ✅ **SearchProvider** 搜索 API 抽象层，支持热插拔
- ✅ **TavilySearchProvider**:
  - Domain whitelisting 支持（基于测试建议）
  - 改进的置信度计算（multi-source 优先级高于 official keywords）
  - 独特域名计数（unique_domains）
  - 完整错误处理（timeout, rate limit, general errors）
- ✅ **SearchTool Fetcher**:
  - 10 分钟缓存（减少 API 调用）
  - 支持 include_domains 参数
  - Provider 热更新接口

### 2. 配置更新 (100%)

#### src/config.py 新增参数

**工具总开关**:
- `DEEP_ANALYSIS_TOOLS_ENABLED`: 启用/禁用工具增强流程（默认 false）
- `DEEP_ANALYSIS_MAX_TOOL_CALLS`: 最大工具轮次（默认 3）
- `DEEP_ANALYSIS_TOOL_TIMEOUT`: 工具超时秒数（默认 10）
- `DEEP_ANALYSIS_TOOL_DAILY_LIMIT`: 每日调用配额（默认 50）

**搜索工具配置**:
- `TOOL_SEARCH_ENABLED`: 搜索工具开关（默认 true）
- `DEEP_ANALYSIS_SEARCH_PROVIDER`: Provider 选择（默认 "tavily"）
- `TAVILY_API_KEY`: Tavily API 密钥
- `SEARCH_MAX_RESULTS`: 最大搜索结果数（默认 5）
- `SEARCH_MULTI_SOURCE_THRESHOLD`: 多源阈值（默认 3）
- `SEARCH_CACHE_TTL_SECONDS`: 缓存TTL（默认 600秒）

**高优先级事件域名白名单**:
```python
HIGH_PRIORITY_EVENT_DOMAINS = {
    "hack": ["coindesk.com", "theblock.co", "cointelegraph.com", "decrypt.co"],
    "regulation": ["coindesk.com", "theblock.co", "theblockcrypto.com"],
    "listing": ["coindesk.com", "theblock.co", "cointelegraph.com"],
    "partnership": ["coindesk.com", "theblock.co"],
}
```

**未来工具预留**:
- `TOOL_PRICE_ENABLED`: 价格工具（Phase 2）
- `TOOL_MACRO_ENABLED`: 宏观工具（Phase 2）
- `TOOL_ONCHAIN_ENABLED`: 链上工具（Phase 2）

---

## 🚧 待实施部分

### 3. LangGraph 集成到 GeminiDeepAnalysisEngine

#### 需要添加到 `src/ai/deep_analysis/gemini.py`:

**A. 状态定义** (文件顶部):
```python
from typing import TypedDict, Optional

class DeepAnalysisState(TypedDict, total=False):
    # 输入
    payload: 'EventPayload'
    preliminary: 'SignalResult'

    # 证据槽位
    search_evidence: Optional[dict]
    memory_evidence: Optional[dict]

    # 控制流
    next_tools: list[str]
    search_keywords: str  # AI 生成的搜索关键词
    tool_call_count: int
    max_tool_calls: int

    # 输出
    final_response: str
```

**B. __init__ 初始化工具** (约 15 行):
```python
def __init__(self, *, client, memory_bundle, parse_json_callback, config=None):
    # ... 现有代码 ...

    self._config = config or SimpleNamespace()
    self._search_tool = None

    # Daily quota tracking
    self._tool_call_daily_limit = getattr(config, "DEEP_ANALYSIS_TOOL_DAILY_LIMIT", 50)
    self._tool_call_count_today = 0
    self._tool_call_reset_date = datetime.now(timezone.utc).date()

    # Initialize search tool if enabled
    if config and getattr(config, "TOOL_SEARCH_ENABLED", False):
        from src.ai.tools import SearchTool
        try:
            self._search_tool = SearchTool(config)
            logger.info("搜索工具已初始化")
        except ValueError as exc:
            logger.warning("搜索工具初始化失败: %s", exc)
```

**C. 节点方法** (约 600 行):

1. `_node_context_gather` - 记忆收集（约 50 行）
2. `_node_tool_planner` - AI 决策 + 关键词生成（约 150 行）
3. `_node_tool_executor` - 工具执行（约 80 行）
4. `_node_synthesis` - 证据综合（约 100 行）
5. Helper 方法：
   - `_fetch_memory_entries` - 记忆检索重构（约 60 行）
   - `_build_planner_prompt` - Planner prompt 构建（约 100 行）
   - `_build_synthesis_prompt` - Synthesis prompt 构建（约 80 行）
   - `_format_*` - 格式化辅助方法（约 80 行）

**D. 路由器方法** (约 30 行):
- `_route_after_planner`
- `_route_after_executor`

**E. 图构建** (约 50 行):
- `_build_deep_graph`

**F. analyse() 方法更新** (约 60 行):
- 添加工具增强流程分支
- 降级到传统 Function Calling

**总代码量**: 约 1,215 行

---

## 📝 实施优先级

### 优先级 1 - 核心流程（必须）

1. **实现 `_fetch_memory_entries` Helper** (任务 3.2)
   - 从现有 `_tool_fetch_memories` 提取核心逻辑
   - 返回格式化的 prompt_entries
   - 复用于 Context Gather 节点和 Function Calling 工具

2. **实现 `_node_tool_planner` 使用 Function Calling** (任务 3.3)
   - 定义 `decide_next_tools` 函数（包含 search_keywords 参数）
   - 构建包含关键词生成规则的 prompt
   - 解析 Function Calling 响应

3. **实现 `_node_tool_executor` 与 domain whitelisting** (任务 3.4)
   - 优先使用 AI 生成的 `search_keywords`
   - 根据 `event_type` 传递 `HIGH_PRIORITY_EVENT_DOMAINS`
   - 提供硬编码降级方案

4. **实现 `_node_synthesis` 与量化置信度调整** (任务 3.5)
   - 构建详细的 synthesis prompt（包含搜索结果详情）
   - 明确置信度调整规则（multi-source +0.15, official +0.10）

5. **实现 `_build_deep_graph` 与路由器** (任务 3.1)
   - 构建 LangGraph 状态机
   - 条件路由逻辑

6. **更新 `analyse()` 方法** (任务 5.1)
   - 检查 `DEEP_ANALYSIS_TOOLS_ENABLED`
   - 调用 LangGraph 或降级到传统流程

### 优先级 2 - 成本控制（强烈建议）

7. **实现每日配额检查** (修改建议 #5)
   - `_check_tool_quota()` 方法
   - 在 `_node_tool_executor` 中调用

8. **实现白名单/黑名单策略** (成本优化章节)
   - `FORCE_SEARCH_EVENT_TYPES`
   - `NEVER_SEARCH_EVENT_TYPES`
   - 在 `_node_tool_planner` 开头应用

### 优先级 3 - 测试与监控（推荐）

9. **单元测试** (任务 1.4)
   - `tests/ai/tools/test_search_fetcher.py`
   - Mock 测试 + 集成测试分离

10. **可观测性增强** (任务 6.5)
    - 详细节点日志
    - 可选：工具调用记录到数据库

---

## 🎯 快速开始指南

### 步骤 1: 创建 `.env.sample` 配置示例

```bash
# ==================== 深度分析工具（第一阶段）====================

# 工具特性开关
DEEP_ANALYSIS_TOOLS_ENABLED=false        # 默认关闭，测试通过后启用

# 工具调用限制
DEEP_ANALYSIS_MAX_TOOL_CALLS=3
DEEP_ANALYSIS_TOOL_TIMEOUT=10
DEEP_ANALYSIS_TOOL_DAILY_LIMIT=50

# 搜索工具配置
TOOL_SEARCH_ENABLED=true
DEEP_ANALYSIS_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-xxxxx                # 替换为实际 API Key
SEARCH_MAX_RESULTS=5
SEARCH_MULTI_SOURCE_THRESHOLD=3
SEARCH_CACHE_TTL_SECONDS=600

# 成本控制
DEEP_ANALYSIS_MONTHLY_BUDGET=30.0
PHASE1_ROLLOUT_PERCENTAGE=0.05           # 5% 流量测试

# 第二阶段+ 工具（暂时禁用）
TOOL_PRICE_ENABLED=false
TOOL_MACRO_ENABLED=false
TOOL_ONCHAIN_ENABLED=false
```

### 步骤 2: 实施 LangGraph 节点（按优先级顺序）

参考 `docs/phase1_search_tool_implementation_cn.md` 第 3-4 天任务：

1. 实现 Context Gather 节点（任务 3.2）
2. 实现 Tool Planner 节点（任务 3.3）
3. 实现 Tool Executor 节点（任务 3.4）
4. 实现 Synthesis 节点（任务 3.5）
5. 构建图结构（任务 3.1）
6. 集成到 analyse() 方法（任务 5.1）

### 步骤 3: 测试验证

```python
# 测试用例
test_messages = [
    "Coinbase 即将上线 XYZ 代币,内部人士透露下周公布",  # 传闻类
    "SEC 批准比特币现货 ETF,将于下周开始交易",         # 政策类
    "XXX DeFi 协议遭受闪电贷攻击,损失超过 $100M USDC",  # 黑客类
]
```

**验证日志**:
```
[INFO] 🧠 Context Gather: 找到 2 条历史事件
[INFO] 🤖 Tool Planner 决策: tools=['search'], keywords='XYZ listing Coinbase official announcement 上线 官方公告'
[INFO] 🔧 调用搜索工具: keyword='XYZ listing Coinbase official announcement 上线 官方公告' (来源: AI生成)
[INFO] 🔧 SearchTool 返回 4 条结果 (multi_source=True, official=True)
[INFO] 📊 Synthesis: 最终置信度 0.65 (初步 0.80)
```

---

## 📊 成本预估（基于已实施的架构）

### 单条消息成本

| 组件 | 成本 | 备注 |
|------|------|------|
| Gemini Flash 初步分析 | $0.0015 | 现有流程 |
| Tool Planner (Gemini Flash) | $0.00004 | 新增，AI 生成关键词 |
| Tavily API (免费层) | $0 | 1000 次/月 |
| Tavily API (Pro 均摊) | $0.02 | $20/月 |
| Synthesis (Gemini Flash) | $0.0001 | 新增 |
| **总计（免费层）** | **$0.00164** | +9% |
| **总计（Pro 层）** | **$0.02164** | +1340% |

### 月度成本（50条/天高价值消息，40%搜索率）

- **现有成本**: $6.75/月
- **Phase 1（免费层）**: $7.47/月 (+$0.72, +11%)
- **Phase 1（Pro 层）**: $18.87/月 (+$12.12, +180%)

**结论**: 使用 Tavily 免费层（月搜索 < 1000次），成本增量仅 **$0.72/月（+11%）**

---

## ✅ 验收标准

### 功能性
- [ ] 传闻/政策/黑客消息触发搜索工具
- [ ] 搜索结果填充 `search_evidence`（multi_source, official_confirmed, sentiment）
- [ ] Synthesis 结合搜索 + 记忆证据调整置信度
- [ ] 搜索失败时降级到传统流程，不阻塞消息处理

### 性能
- [ ] 平均延迟 < 8s
- [ ] Tavily API 成功率 > 95%
- [ ] 每条消息工具调用 ≤ 1 次

### 成本
- [ ] 平均成本 < $0.05/条
- [ ] Tavily 月度配额在限制内（1,000 免费或 $20 无限量）

### 质量
- [ ] 传闻消息置信度准确性提升
- [ ] 误报率降低（多源验证过滤虚假传闻）
- [ ] Synthesis 的 `notes` 字段包含搜索来源引用

---

## 🔗 相关文档

- **实施指南**: `docs/phase1_search_tool_implementation_cn.md`
- **整体方案**: `docs/deep_analysis_tools_integration_plan.md`
- **API 测试**: `docs/tavily_api_response_format.md`
- **测试脚本**: `scripts/test_tavily_api.py`
- **架构审查**: 见前述对话总结

---

## 📅 下一步行动

1. **立即开始**: 实施 LangGraph 节点方法（参考优先级 1 列表）
2. **并行测试**: 使用 `scripts/test_tavily_api.py` 验证 API 可用性
3. **渐进部署**: 先 5% 流量测试，验证成本和效果后扩展到 100%
4. **监控指标**: 每日 Tavily 调用次数、平均成本、置信度改善幅度

**预计完成时间**: 2-3 天（核心流程） + 1-2 天（测试优化）
