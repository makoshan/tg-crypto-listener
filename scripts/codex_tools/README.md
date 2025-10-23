# Codex CLI 工具集

这些工具为 Codex CLI Agent 提供标准化的命令行接口，用于新闻搜索和历史记忆检索。

## 重要说明

这些 CLI 工具**仅供 Codex CLI Engine 使用**，因为 Codex Agent 运行在沙盒环境中，只能通过 bash 命令与外部交互。

**Gemini Engine 不需要这些 CLI 工具**，因为它直接调用 Python 函数（`SearchTool.fetch()`, `PriceTool.snapshot()` 等）。

两个引擎使用**相同的底层工具实现**，只是调用方式不同：
- **Codex CLI**：bash 命令 → CLI 工具 → 底层服务
- **Gemini**：Function Calling → 直接调用 → 底层服务

---

## 工具列表

### 1. search_news.py - 新闻搜索工具

通过 Tavily API 搜索新闻并返回标准 JSON 格式。

**用途**：
- 验证事件真实性
- 获取多源新闻确认
- 发现事件关键细节

**使用方法**：
```bash
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "关键词" \
    --max-results 6 \
    [--domains domain1.com domain2.com]
```

**参数**：
- `--query`: 搜索关键词（必需）
- `--max-results`: 最大结果数量（默认：6）
- `--domains`: 限制搜索的域名列表（可选）

**输出格式**：
```json
{
  "success": true,
  "data": {
    "keyword": "搜索关键词",
    "source_count": 5,
    "multi_source": true,
    "official_confirmed": true,
    "sentiment": { "positive": 3, "negative": 1, "neutral": 1 },
    "results": [
      {
        "title": "新闻标题",
        "url": "https://...",
        "content": "新闻内容",
        "source": "来源网站",
        "score": 0.85,
        "published_date": "2025-01-22"
      }
    ]
  },
  "confidence": 0.85,
  "triggered": true,
  "error": null
}
```

**失败输出**：
```json
{
  "success": false,
  "data": null,
  "confidence": 0.0,
  "triggered": false,
  "error": "错误描述"
}
```

**示例**：
```bash
# 搜索 Binance 上币公告
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "Binance ABC token listing official announcement" \
    --max-results 6

# 搜索监管新闻（限定特定域名）
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "SEC Bitcoin ETF approval" \
    --max-results 5 \
    --domains sec.gov coindesk.com
```

---

### 2. fetch_price.py - 价格数据工具

批量获取多个加密资产的实时价格数据。

**用途**：
- 获取资产实时价格
- 验证价格异常或涨跌
- 评估市场反应

**使用方法**：
```bash
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
    --assets 资产1 资产2 资产3 \
    [--force-refresh]
```

**参数**：
- `--assets`: 资产符号列表（必需，可多个，例如：BTC ETH SOL）
- `--force-refresh`: 强制刷新缓存（可选）

**输出格式**：
```json
{
  "success": true,
  "count": 3,
  "assets": [
    {
      "asset": "BTC",
      "success": true,
      "price": 107817.37,
      "price_change_24h": -0.68,
      "price_change_1h": 0.12,
      "price_change_7d": 2.34,
      "market_cap": 2134567890000,
      "volume_24h": 45678901234,
      "confidence": 0.95,
      "triggered": true,
      "timestamp": "2025-01-22T13:00:00Z"
    },
    {
      "asset": "ETH",
      "success": true,
      "price": 3245.67,
      "price_change_24h": 1.23,
      ...
    }
  ]
}
```

**失败输出**（单个资产失败）：
```json
{
  "success": false,
  "count": 1,
  "assets": [
    {
      "asset": "INVALID",
      "success": false,
      "error": "Asset not found"
    }
  ]
}
```

**示例**：
```bash
# 获取单个资产价格
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
    --assets BTC

# 获取多个资产价格（批量）
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
    --assets BTC ETH SOL USDC

# 强制刷新缓存
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
    --assets BTC ETH \
    --force-refresh
```

---

### 3. fetch_memory.py - 历史记忆检索工具

从混合记忆存储（Supabase + 本地）中检索相似历史事件。

**用途**：
- 查找历史相似案例
- 参考过去的处理方式
- 评估事件独特性

**使用方法**：
```bash
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "主题描述" \
    [--asset 资产代码] \
    [--limit 数量]
```

**参数**：
- `--query`: 查询文本（必需）
- `--asset`: 资产代码过滤（可选，例如：BTC, USDC）
- `--limit`: 最大返回数量（默认：3）

**输出格式**：
```json
{
  "success": true,
  "entries": [
    {
      "id": "事件ID",
      "summary": "事件摘要",
      "action": "buy|sell|observe",
      "confidence": 0.85,
      "similarity": 0.82,
      "assets": ["BTC", "ETH"],
      "evidence": "验证证据",
      "timestamp": "2025-01-20T10:00:00Z"
    }
  ],
  "similarity_floor": 0.75,
  "message": "Retrieved 2 memory entries"
}
```

**失败输出**：
```json
{
  "success": false,
  "entries": [],
  "similarity_floor": null,
  "message": "错误描述"
}
```

**示例**：
```bash
# 检索 USDC 脱锚历史案例
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "USDC depeg risk" \
    --asset USDC \
    --limit 3

# 检索比特币价格相关历史
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "Bitcoin price surge" \
    --asset BTC \
    --limit 5
```

---

## 工具调用规则（供 Codex Agent 参考）

### ✅ 必须遵守

1. **记录执行**：将执行的命令、关键数据、证据来源写入分析的 `notes` 字段
2. **使用输出**：使用 JSON 输出中的数据来支持分析（引用 source、confidence、links 等）
3. **证据引用**：在 notes 中明确引用工具返回的证据

### ✅ 建议使用

1. **搜索工具**：验证高优先级事件（hack、regulation、partnership、listing）
2. **搜索工具**：消息是传闻或缺乏来源时
3. **记忆工具**：需要历史案例参考时

### ⚠️ 禁止行为

1. **不要**直接调用 Tavily HTTP API 或其他外部 API
2. **不要**伪造数据或在没有执行命令的情况下声称已验证
3. **不要**忽略工具返回的 `success=false` 错误

### 失败处理

- 如果脚本返回 `success=false`，检查 `error` 字段了解失败原因
- 可以尝试调整查询关键词后重试（例如：简化关键词、使用英文）
- 如果重试后仍然失败，在 notes 中说明"工具调用失败，依据初步分析"
- 工具失败不应阻止完成分析，但应降低置信度并标注 `data_incomplete`

---

## 测试

运行测试脚本验证工具功能：

```bash
bash scripts/test_codex_tools.sh
```

或手动测试：

```bash
# 测试搜索工具
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "Bitcoin ETF approval" \
    --max-results 3

# 测试记忆工具
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "Bitcoin price" \
    --asset BTC \
    --limit 2
```

---

## 环境要求

这些工具依赖于主项目配置和环境变量：

- `TAVILY_API_KEY`: Tavily 搜索 API 密钥（用于 search_news.py）
- `MEMORY_ENABLED=true`: 启用记忆系统（用于 fetch_memory.py）
- `MEMORY_BACKEND=hybrid`: 使用混合记忆存储（推荐）
- `SUPABASE_URL` 和 `SUPABASE_SERVICE_KEY`: Supabase 配置（如使用 Supabase 记忆）

确保 `.env` 文件包含这些配置。

---

## 故障排除

### search_news.py 失败

**问题**：`success: false, error: "Tavily API key not configured"`
**解决**：检查 `.env` 文件中的 `TAVILY_API_KEY` 配置

**问题**：`success: false, error: "API quota exceeded"`
**解决**：等待配额重置或升级 Tavily 套餐

### fetch_memory.py 失败

**问题**：`success: true, entries: [], message: "Memory system is disabled"`
**解决**：设置 `MEMORY_ENABLED=true` 并配置 `MEMORY_BACKEND`

**问题**：Supabase 连接失败
**解决**：检查 `SUPABASE_URL` 和 `SUPABASE_SERVICE_KEY`，或使用 `MEMORY_BACKEND=local` 降级到本地存储

---

## 集成到 Codex CLI 深度分析

这些工具已自动集成到 `CodexCliEngine`（`src/ai/deep_analysis/codex_cli.py`）的提示词中。

Codex Agent 会在分析提示词中看到工具使用守则，并可以自主决定何时调用这些工具。

查看 `src/ai/deep_analysis/codex_cli.py` 中的 `_build_cli_prompt()` 方法了解完整集成细节。
