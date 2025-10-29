# 深度分析工具接入总纲（合并版）

> 合并来源：`phase1_tooling_integration.md`、`price_tool_integration_summary.md`、`tavily_api_response_format.md`、`tooling_integration_overview.md`

## 1. 概述
- LangGraph 深度分析子图接入搜索/价格/宏观/链上/协议等工具，采用统一抽象、注册表与 fetcher 缓存。
- 搜索支持 Tavily（默认）与 Brave（可切换）；价格工具支持 CoinGecko/CoinMarketCap。

## 2. 架构与统一设计
- 抽象接口：`src/ai/tools/<category>/providers/base.py` 定义 `*Provider`。
- 注册表与工厂：`src/ai/tools/<category>/__init__.py` 中 `REGISTRY` 与 `create_*_provider(config)`。
- Facade/Fetcher：`src/ai/tools/<category>/fetcher.py` 封装缓存、日志与统一入口。
- LangGraph 节点：ContextGather → ToolPlanner → ToolExecutor → Synthesis，Router 控制循环与降级。

## 3. 搜索工具（Search）
- 入口：`src/ai/tools/search/fetcher.py` → `SearchTool.fetch(keyword, max_results, include_domains)`
- 提供商：
  - Tavily（默认）：`src/ai/tools/search/providers/tavily.py`
  - Brave（可切换）：见下文“3.3 Brave 接入说明”（`https://api-dashboard.search.brave.com/app/documentation/web-search/get-started`）
- 注册表：`src/ai/tools/search/__init__.py`（`REGISTRY`）
- 输出字段：`multi_source`、`official_confirmed`、`sentiment`、`triggered`、`confidence`、`source_count`
- 域名白名单：通过 `include_domains`（Tavily 服务端支持；Brave 侧在客户端过滤）

### 3.1 Tavily 响应格式（提要）
- 顶层字段：`query`、`results[]`、`response_time`、`request_id` 等
- 结果字段：`title`、`url`、`content`、`score`
- 实测：域名白名单显著提升质量；平均响应 1.5–2.5s；评分 0.5–0.7 合理
- 解析建议：以唯一域名数计算 `multi_source`；触发条件可用“多源 + 平均评分≥0.6”

### 3.2 Brave 对齐要点（提要）
- 端点：`https://api.search.brave.com/res/v1/web/search`
- 认证：`X-Subscription-Token: <API_KEY>`
- `include_domains` 由客户端过滤，输出保持与 Tavily 一致的结构与信号

### 3.3 Brave 接入说明（详细）
- 官方文档：`https://api-dashboard.search.brave.com/app/documentation/web-search/get-started`
- 端点与认证
  - 端点：`https://api.search.brave.com/res/v1/web/search`
  - 请求头：`Accept: application/json`、`Accept-Encoding: gzip`、`X-Subscription-Token: <YOUR_API_KEY>`
  - 示例（curl）：
    ```bash
    curl -s --compressed "https://api.search.brave.com/res/v1/web/search?q=bitcoin+etf" \
      -H "Accept: application/json" \
      -H "Accept-Encoding: gzip" \
      -H "X-Subscription-Token: $BRAVE_API_KEY"
    ```
- 关键参数（常用）：
  - `q`: 查询字符串（支持中英文）
  - `count`: 返回条数（建议与 `SEARCH_MAX_RESULTS` 对齐）
  - `search_lang`, `country`: 语言/区域（按需）
  - 域名白名单：Brave 暂无直通参数，建议客户端按 `results[].url` 的域名做过滤；但严格白名单可能导致 0 命中（建议改用 `site:` 语法）
  - 站点限定（推荐）：直接在查询中使用 `site:coindesk.com`、`site:reuters.com` 等可显著提升命中质量；优先于客户端白名单
- 响应映射：
  - Web 结果通常位于 `web.results` 数组，包含 `title`, `url`, `meta_url.host` 等
  - 统一映射为：`{"title": str, "source": host, "url": str, "score": 0.0}`

#### 实测提示
- 通用查询（如“bitcoin spot etf”）在无白名单时可返回财经站点（etfdb、blackrock、investopedia、yahoo finance 等），质量较稳定
- 使用客户端白名单过滤至加密媒体/主流媒体时，可能 0 命中；建议改为查询内 `site:` 约束（如 `SEC approves bitcoin spot etf site:coindesk.com`）
- 示例：`solana etf` 可命中 Yahoo Finance、ProShares、Volatility Shares 等站点；若需要新闻媒体，建议使用 `site:coindesk.com`/`site:reuters.com` 等限定
- 环境变量与配置：
  ```bash
  DEEP_ANALYSIS_SEARCH_PROVIDER=brave
  BRAVE_API_KEY=brv-xxxxx
  SEARCH_MAX_RESULTS=5
  SEARCH_MULTI_SOURCE_THRESHOLD=3
  SEARCH_CACHE_TTL_SECONDS=600
  ```

## 4. 价格工具（Price）
- 入口：`src/ai/tools/price/fetcher.py` → `PriceTool`
- Provider：`coingecko`、`coinmarketcap`
- 深度分析流集成：
  - 在 Engine 初始化受 `TOOL_PRICE_ENABLED` 与 `DEEP_ANALYSIS_PRICE_PROVIDER` 控制
  - ToolExecutor 增加 `price` 分支，返回 `price_evidence`，在 Synthesis 中格式化与加权
- Prompt 与决策：
  - Tool Planner 使用 AI 决策是否调用 search/price，无硬编码规则
  - 价格异常可提升置信度，价格与事件冲突则降低置信度并标记冲突

## 5. 配置清单（.env 建议）
```bash
DEEP_ANALYSIS_TOOLS_ENABLED=false
DEEP_ANALYSIS_MAX_TOOL_CALLS=3
DEEP_ANALYSIS_TOOL_TIMEOUT=10
DEEP_ANALYSIS_TOOL_DAILY_LIMIT=50

TOOL_SEARCH_ENABLED=true
DEEP_ANALYSIS_SEARCH_PROVIDER=tavily   # 可选: tavily | brave
TAVILY_API_KEY=tvly-xxxxx
BRAVE_API_KEY=brv-xxxxx
SEARCH_MAX_RESULTS=5
SEARCH_MULTI_SOURCE_THRESHOLD=3
SEARCH_CACHE_TTL_SECONDS=600

DEEP_ANALYSIS_MONTHLY_BUDGET=30.0
PHASE1_ROLLOUT_PERCENTAGE=0.05

TOOL_PRICE_ENABLED=false
DEEP_ANALYSIS_PRICE_PROVIDER=coingecko
# COINGECKO_API_KEY=your-api-key-here

TOOL_MACRO_ENABLED=false
DEEP_ANALYSIS_MACRO_PROVIDER=fred

TOOL_ONCHAIN_ENABLED=false
DEEP_ANALYSIS_ONCHAIN_PROVIDER=defillama

TOOL_PROTOCOL_ENABLED=false
DEEP_ANALYSIS_PROTOCOL_PROVIDER=defillama
```

## 6. 流程与样例（合成）
- Tool Planner（AI）→ 选择 `search`/`price`
- Tool Executor：
  - SearchTool：按事件类型传递域名白名单，产出多源/官方/情绪等信号
  - PriceTool：拉取资产快照，提供去锚/波动/偏离等特征
- Synthesis：基于证据加减置信度并生成最终文本

## 7. 测试与监控
- 单测：
  - `tests/ai/tools/test_search_fetcher.py`（Mock Tavily/Brave 响应、缓存命中）
  - `tests/ai/tools/test_price_tool.py`（格式化与快照）
- 集成：覆盖 LangGraph 节点主链路，验证 tools、keywords、notes 输出
- 监控：记录 sources、multi_source、official_confirmed、triggered、confidence、缓存命中与耗时

### 7.1 Brave 快速测试脚本（本仓库脚本）
- 文件：`scripts/test_brave_api.py`
- 依赖：`httpx`（已在项目中使用）
- 运行示例：
  ```bash
  # 方式一：通过环境变量传入 API Key（推荐）
  BRAVE_API_KEY=BSABomBVcnDm05R7ttT9MvzU_hQ2ook \
  uvx --with-requirements requirements.txt python scripts/test_brave_api.py \
    --q "bitcoin spot etf" --count 5 --include-domains coindesk.com theblock.co

  # 方式二：通过参数传入 API Key
  uvx --with-requirements requirements.txt python scripts/test_brave_api.py \
    --q "USDC depeg 官方声明" --count 5 --api-key BSABomBVcnDm05R7ttT9MvzU_hQ2ook \
    --include-domains coindesk.com cointelegraph.com theblock.co
  ```
  - 输出：标准化结果（title/source/url/score），含原始顶层键与 HTTP 状态，便于排错
  - 说明：`--include-domains` 会在客户端侧按域名过滤，行为与 Tavily 的 `include_domains` 一致；若出现 0 命中，优先尝试在查询中加入 `site:domain.com`

示例（提高命中质量）：
```bash
# 使用 site 语法限定到 CoinDesk（更稳定）
BRAVE_API_KEY=$KEY uvx --with-requirements requirements.txt python scripts/test_brave_api.py \
  --q "SEC approves bitcoin spot etf site:coindesk.com" --count 5

# 搜索 solana etf（通用），如需媒体来源可改为 site 语法
BRAVE_API_KEY=$KEY uvx --with-requirements requirements.txt python scripts/test_brave_api.py \
  --q "solana etf" --count 5
```

## 8. 实施与优化建议
- 对 hack/regulation/listing/partnership 等高优先级事件，强制域名白名单
- `multi_source` 权重高于“官方关键词”；可将官方加权降到 +0.10
- 控制成本：灰度（`PHASE1_ROLLOUT_PERCENTAGE`）、配额（`DEEP_ANALYSIS_TOOL_DAILY_LIMIT`）

## 9. 参考与链接
- Tavily API：`https://docs.tavily.com/reference/search`
- Brave Web Search API：`https://api-dashboard.search.brave.com/app/documentation/web-search/get-started`
