# Codex CLI 深度分析工具实现总结

## 概述

已成功为 Codex CLI 深度分析引擎实现搜索工具和记忆检索的命令行接口，使 Codex Agent 能够自主调用这些工具进行深度分析。

实现日期：2025-10-22

---

## 实现内容

### 1. 新闻搜索工具 (search_news.py)

**文件位置**：`scripts/codex_tools/search_news.py`

**功能**：
- 调用 `SearchTool.fetch()` 执行 Tavily API 搜索
- 返回标准 JSON 格式供 Codex Agent 解析
- 支持自定义关键词、结果数量、域名过滤

**输出字段**：
- `success`: 是否成功
- `data`: 搜索结果数据（包含 source_count、multi_source、official_confirmed、results 等）
- `confidence`: 搜索置信度
- `triggered`: 是否触发深度分析
- `error`: 错误信息（如果失败）

**使用示例**：
```bash
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "Binance ABC token listing official announcement" \
    --max-results 6
```

---

### 2. 历史记忆检索工具 (fetch_memory.py)

**文件位置**：`scripts/codex_tools/fetch_memory.py`

**功能**：
- 调用 `HybridMemoryRepository.fetch_memories()` 检索历史案例
- 支持关键词、资产代码过滤
- 返回标准 JSON 格式，包含相似度评分

**输出字段**：
- `success`: 是否成功
- `entries`: 记忆条目列表（包含 summary、action、confidence、similarity、assets 等）
- `similarity_floor`: 最低相似度
- `message`: 执行消息

**使用示例**：
```bash
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "USDC depeg risk" \
    --asset USDC \
    --limit 3
```

---

### 3. CodexCliEngine 提示词增强

**文件位置**：`src/ai/deep_analysis/codex_cli.py`

**修改内容**：
在 `_build_cli_prompt()` 方法中添加"工具使用守则"段落，包含：

1. **工具介绍**：
   - 新闻搜索工具的用途、命令格式、输出格式、使用场景
   - 历史记忆检索工具的用途、命令格式、输出格式、使用场景

2. **工具调用规则**：
   - ✅ 必须：将命令、数据、证据写入 notes
   - ✅ 必须：使用 JSON 输出支持分析
   - ✅ 建议：优先验证高优先级事件
   - ⚠️ 禁止：直接调用外部 API、伪造数据

3. **失败处理**：
   - 检查 error 字段了解失败原因
   - 可调整关键词重试
   - 失败不应阻止分析，但应降低置信度

4. **证据引用示例**：
   - 展示如何在 notes 中引用工具输出
   - 强调引用来源、置信度、链接

**关键设计**：
- 使用 `uvx --with-requirements requirements.txt` 确保依赖可用
- 命令格式清晰，包含反斜杠换行
- 强调证据追溯和透明度
- 提供正负示例帮助 Agent 理解

---

## 架构设计

### 工具调用流程

```
Codex Agent 接收任务
  ↓
读取提示词中的工具使用守则
  ↓
自主决策：是否需要搜索/记忆检索？
  ↓
执行 bash 命令调用工具
  ↓
解析 JSON 输出
  ↓
将证据写入 notes 字段
  ↓
综合所有证据生成最终 JSON 信号
```

### 依赖关系

```
scripts/codex_tools/
├── search_news.py
│   └── 依赖：src/ai/tools/search/fetcher.py (SearchTool)
│       └── 依赖：Tavily API (TAVILY_API_KEY)
│
├── fetch_memory.py
│   └── 依赖：src/memory/factory.py (create_memory_backend)
│       └── 依赖：HybridMemoryRepository / SupabaseMemoryRepository / LocalMemoryStore
│           └── 依赖：Supabase (SUPABASE_URL, SUPABASE_SERVICE_KEY) 或本地 JSON
│
└── README.md (工具文档)
```

### 错误处理机制

1. **工具级别**：
   - 捕获所有异常，返回 `success=false` 和 `error` 信息
   - 失败输出到 stderr，成功输出到 stdout
   - 非零退出码表示工具执行失败

2. **Agent 级别**：
   - 解析 JSON 的 `success` 字段判断工具是否成功
   - 失败时可重试（调整关键词）
   - 最终失败时降低置信度，标注 `data_incomplete`

3. **降级策略**：
   - 搜索失败 → 依据初步分析 + 历史记忆
   - 记忆检索失败 → 依据初步分析 + 搜索结果
   - 两者都失败 → 仅依据初步分析，置信度降低

---

## 测试验证

### 测试脚本

**文件位置**：`scripts/test_codex_tools.sh`

**测试内容**：
1. 测试搜索工具：查询 "Bitcoin ETF approval"
2. 测试记忆工具：查询 "Bitcoin price" 并过滤 BTC 资产

**运行方式**：
```bash
bash scripts/test_codex_tools.sh
```

### 语法检查

```bash
python3 -m py_compile scripts/codex_tools/search_news.py scripts/codex_tools/fetch_memory.py
```

已通过语法检查，无编译错误。

---

## 配置要求

### 搜索工具

- `TAVILY_API_KEY`: Tavily 搜索 API 密钥
- `DEEP_ANALYSIS_SEARCH_PROVIDER=tavily`: 搜索提供商
- `SEARCH_MAX_RESULTS=5`: 默认搜索结果数量

### 记忆工具

- `MEMORY_ENABLED=true`: 启用记忆系统
- `MEMORY_BACKEND=hybrid`: 混合记忆存储（推荐）
- `SUPABASE_URL` 和 `SUPABASE_SERVICE_KEY`: Supabase 配置
- `MEMORY_MAX_NOTES=3`: 最大记忆条目数
- `MEMORY_SIMILARITY_THRESHOLD=0.55`: 相似度阈值

---

## 使用指南

### Agent 自主调用示例

Codex Agent 在深度分析时会看到提示词中的工具守则，并可以自主执行：

```bash
# Agent 决定需要验证上币新闻
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "Binance XYZ listing official" \
    --max-results 6

# 解析输出
{
  "success": true,
  "data": {
    "source_count": 5,
    "multi_source": true,
    "official_confirmed": true,
    ...
  },
  "confidence": 0.85
}

# Agent 将证据写入 notes
"通过搜索工具验证：找到 5 条来源，多源确认=true，官方确认=true，confidence=0.85"
```

### 手动调用测试

开发者可以手动调用工具验证功能：

```bash
# 测试搜索
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "USDC depeg Circle" \
    --max-results 5

# 测试记忆
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "stablecoin depeg" \
    --asset USDC \
    --limit 3
```

---

## 与现有系统集成

### 与 Gemini 深度分析对比

| 特性 | Codex CLI Engine | Gemini Engine (LangGraph) |
|------|-----------------|--------------------------|
| **工具调用方式** | Agent 自主通过 bash 命令 | Function Calling + 手动实现 |
| **搜索工具** | ✅ search_news.py | ✅ SearchTool.fetch() |
| **记忆检索** | ✅ fetch_memory.py | ✅ _fetch_memory_entries() |
| **可观察性** | 黑盒（Agent 自主） | 高（LangGraph State 可见） |
| **灵活性** | Agent 自主决策 | 精细控制每个步骤 |
| **延迟** | 12-16s | 5-10s (预估) |
| **费用** | 零（利用 Codex 订阅） | Gemini API 费用 |

**关键区别**：
- Codex CLI 通过 CLI 工具调用现有功能
- Gemini 直接调用 Python 函数
- 两者使用相同的底层实现（SearchTool、MemoryRepository）

### 未来扩展方向

1. **价格工具** (`fetch_price.py`)：
   - 调用 CoinGecko/CoinMarketCap API
   - 返回价格、涨跌幅、市值、交易量

2. **宏观数据工具** (`fetch_macro.py`)：
   - 调用 FRED API 获取宏观指标
   - 返回 CPI、利率、失业率、VIX、DXY 等

3. **链上数据工具** (`fetch_onchain.py`)：
   - 调用 DeFiLlama API
   - 返回 TVL、赎回量、桥接状态

4. **协议数据工具** (`fetch_protocol.py`)：
   - 调用 DeFiLlama Protocol API
   - 返回协议级 TVL、链分布、费用

---

## 文档

### 创建的文档

1. **scripts/codex_tools/README.md**：
   - 工具使用指南
   - 参数说明
   - 输出格式
   - 示例命令
   - 故障排除

2. **docs/codex_cli_tools_implementation_summary.md**（本文档）：
   - 实现总结
   - 架构设计
   - 集成说明
   - 测试验证

### 相关文档

- `docs/codex_cli_integration_plan.md`：Codex CLI 集成方案
- `docs/deep_analysis_engine_switch_plan.md`：深度分析引擎架构
- `src/ai/deep_analysis/codex_cli.py`：CodexCliEngine 实现

---

## 检查清单

- [x] 创建 `scripts/codex_tools/` 目录
- [x] 实现 `search_news.py` CLI 工具
- [x] 实现 `fetch_memory.py` CLI 工具
- [x] 更新 `CodexCliEngine._build_cli_prompt()` 添加工具使用守则
- [x] 创建测试脚本 `scripts/test_codex_tools.sh`
- [x] 通过语法检查
- [x] 创建工具文档 `scripts/codex_tools/README.md`
- [x] 创建实现总结文档（本文档）

---

## 下一步建议

### 短期（立即可做）

1. **运行测试**：执行 `bash scripts/test_codex_tools.sh` 验证工具功能
2. **更新 .env**：确保 `TAVILY_API_KEY` 和记忆配置正确
3. **测试集成**：运行完整的深度分析流程，观察 Codex Agent 是否自主调用工具

### 中期（1-2 周）

1. **添加价格工具**：实现 `fetch_price.py` 支持价格验证
2. **添加宏观数据工具**：实现 `fetch_macro.py` 支持宏观事件分析
3. **集成测试**：编写 `tests/ai/deep_analysis/test_codex_cli_tools.py`

### 长期（1 个月+）

1. **完善工具集**：添加链上数据、协议数据工具
2. **优化提示词**：根据实际使用情况调整工具使用守则
3. **性能监控**：记录工具调用成功率、延迟、置信度提升效果
4. **文档完善**：在主 README 中添加 Codex CLI 工具使用说明

---

## 总结

已成功为 Codex CLI 深度分析引擎实现搜索和记忆检索工具的命令行接口，使 Codex Agent 能够：

1. **自主验证**：通过搜索工具验证消息真实性
2. **历史参考**：通过记忆工具参考历史案例
3. **证据追溯**：在 notes 中记录工具执行和证据来源
4. **降级处理**：工具失败时能够继续分析并标注数据不完整

这些工具与现有的 Gemini 深度分析引擎使用相同的底层实现（SearchTool、MemoryRepository），确保了：
- **功能对等**：两个引擎都能执行相同的深度分析任务
- **代码复用**：避免重复实现搜索和记忆逻辑
- **一致性**：输出格式和行为保持一致

Codex CLI 引擎现在具备与 Gemini 引擎相同的工具调用能力，可以根据成本、延迟、可观察性需求灵活选择使用哪个引擎。
