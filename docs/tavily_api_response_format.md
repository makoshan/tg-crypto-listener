# Tavily API 响应格式文档

**测试日期**: 2025-10-11
**API Key**: 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
**测试脚本**: `scripts/test_tavily_api.py`

---

## 📊 实际响应结构

### 顶层字段

```json
{
  "query": "用户的搜索查询",
  "follow_up_questions": null,
  "answer": null,
  "images": [],
  "results": [...],
  "response_time": 1.84,
  "request_id": "cb683bd8-532c-4289-b7f6-9f27a826e962"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | `str` | 返回用户提交的原始查询 |
| `follow_up_questions` | `None` | 跟进问题（当 `include_answer=false` 时为 null） |
| `answer` | `None` | AI 生成的答案（当 `include_answer=false` 时为 null） |
| `images` | `list` | 相关图片列表（空数组） |
| `results` | `list[dict]` | **核心搜索结果数组** |
| `response_time` | `float` | API 响应时间（秒），范围：1.0 - 2.5s |
| `request_id` | `str` | 请求唯一标识符，用于追踪 |

---

## 🔍 搜索结果 (`results`) 字段

每个结果条目包含以下字段：

```json
{
  "title": "Cointelegraph USDC depegs as Circle confirms $3.3B stuck with Silicon Valley Bank",
  "url": "https://cointelegraph.com/news/usdc-depegs-as-circle-confirms-3-3b-stuck-with-silicon-valley-bank",
  "content": "March 11, 2023 - USDC has lost over 10% of its value as it trades at $0.8774, while on-chain data reveals that Circle redeemed a net of $1.4 billion in USDC in 8 hours.",
  "score": 0.67166495,
  "raw_content": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | `str` | 文章标题，可能包含来源名（如 "Cointelegraph ..."） |
| `url` | `str` | 文章完整 URL |
| `content` | `str` | 文章摘要/片段，通常包含日期和关键信息，长度约 100-200 字符 |
| `score` | `float` | Tavily 相关性评分，范围：0.0 - 1.0，越高越相关 |
| `raw_content` | `None` | 完整文章内容（默认为 null，需额外参数开启） |

---

## 📈 测试结果统计

### 测试用例 1: USDC 脱锚事件

**查询**: `"USDC Circle depeg official statement 脱锚 官方声明"`
**限制域名**: `["coindesk.com", "theblock.co", "cointelegraph.com"]`

- **响应时间**: 2.48s
- **结果数量**: 5 条
- **来源分布**:
  - cointelegraph.com: 3 条
  - www.theblock.co: 2 条
- **包含官方关键词**: 1/5 (20%)
- **平均评分**: 0.56
- **评分范围**: 0.51 - 0.67

**关键发现**:
- ✅ 中英文混合查询有效
- ✅ 成功限制到指定域名
- ⚠️ 官方关键词匹配率较低（可能是历史新闻，非实时官方声明）

---

### 测试用例 2: 比特币 ETF 批准

**查询**: `"Bitcoin spot ETF SEC approval 比特币 现货 批准"`
**限制域名**: `["coindesk.com", "theblock.co"]`

- **响应时间**: 1.59s
- **结果数量**: 5 条
- **来源分布**:
  - www.theblock.co: 3 条
  - www.coindesk.com: 2 条
- **包含官方关键词**: 0/5 (0%)
- **平均评分**: 0.65
- **评分范围**: 0.60 - 0.73

**关键发现**:
- ✅ 响应速度快（1.59s）
- ✅ 评分较高（平均 0.65）
- ✅ 成功限制到 2 个指定域名
- ⚠️ 无官方关键词（可能是新闻报道，非 SEC 官方文件）

---

### 测试用例 3: Binance 黑客攻击

**查询**: `"Binance hack exploit $50M 黑客攻击"`
**限制域名**: 无限制

- **响应时间**: 1.84s
- **结果数量**: 3 条
- **来源分布**:
  - www.ballet.com: 1 条
  - www.coingabbar.com: 1 条
  - t.me: 1 条（Telegram 频道）
- **包含官方关键词**: 0/3 (0%)
- **平均评分**: 0.44
- **评分范围**: 0.16 - 0.64

**关键发现**:
- ⚠️ 未限制域名时，来源质量参差不齐
- ⚠️ 包含非权威来源（t.me）
- ⚠️ 评分较低（最低 0.16）
- 💡 **建议**: 对 hack/exploit 事件，强制限制权威域名

---

## 🎯 关键结论

### 1. 响应时间稳定

- **平均响应时间**: 1.97s
- **范围**: 1.59s - 2.48s
- **结论**: 符合文档预期的 1-2 秒延迟

### 2. 域名过滤有效

- ✅ `include_domains` 参数**严格生效**
- ✅ 所有结果都来自指定域名
- 💡 **建议**: 始终设置权威域名白名单

### 3. 评分分布

| 场景 | 平均评分 | 评分范围 |
|------|---------|---------|
| 限制权威域名 + 明确事件 | 0.56 - 0.65 | 0.51 - 0.73 |
| 无域名限制 | 0.44 | 0.16 - 0.64 |

**结论**: 限制权威域名能显著提升结果质量

### 4. 官方关键词检测

- **测试 1**: 1/5 (20%) - 包含 "confirms" 官方动词
- **测试 2**: 0/5 (0%) - SEC 批准新闻，非官方文件
- **测试 3**: 0/3 (0%) - 黑客攻击新闻报道

**结论**:
- ⚠️ "官方关键词" 检测不可靠作为唯一置信度指标
- 💡 应结合 **多源确认** + **评分阈值** + **域名权威性**

### 5. 中英文混合查询

- ✅ 中英文混合查询**完全有效**
- ✅ Tavily 能同时匹配中文和英文内容
- 💡 **最佳实践**: `"USDC Circle depeg 脱锚 official statement 官方声明"`

---

## 🔧 实施建议

### 1. 优化 `TavilySearchProvider._parse_response()`

基于实际测试，需要调整解析逻辑：

```python
def _parse_response(self, data: dict, keyword: str) -> ToolResult:
    results = data.get("results", [])

    # ✅ 调整多源阈值判断：基于域名去重
    from urllib.parse import urlparse
    unique_domains = set(urlparse(r.get("url", "")).netloc for r in results)
    multi_source = len(unique_domains) >= self._multi_source_threshold

    # ✅ 官方确认检测：扩展关键词
    official_confirmed = self._check_official_confirmation(results)

    # ✅ 情绪分析：保持现有逻辑
    sentiment = self._analyze_sentiment(results)

    # ✅ 格式化结果：提取域名
    formatted_results = [
        {
            "title": item.get("title", ""),
            "source": urlparse(item.get("url", "")).netloc,
            "url": item.get("url", ""),
            "score": item.get("score", 0.0),
        }
        for item in results
    ]

    tool_data = {
        "keyword": keyword,
        "results": formatted_results,
        "multi_source": multi_source,
        "official_confirmed": official_confirmed,
        "sentiment": sentiment,
        "source_count": len(results),
        "unique_domains": len(unique_domains),  # 🆕 添加唯一域名数
    }

    # ✅ 触发条件：多源 + 高评分（放宽官方确认要求）
    avg_score = sum(r.get("score", 0) for r in results) / len(results) if results else 0
    triggered = multi_source and avg_score >= 0.6

    confidence = self._calculate_confidence(results, multi_source, official_confirmed)

    return ToolResult(
        source="Tavily",
        timestamp=ToolResult._format_timestamp(),
        success=True,
        data=tool_data,
        triggered=triggered,
        confidence=confidence,
    )
```

---

### 2. 置信度计算优化

基于测试评分分布：

```python
def _calculate_confidence(
    self,
    results: list[dict],
    multi_source: bool,
    official_confirmed: bool,
) -> float:
    if not results:
        return 0.0

    avg_score = sum(item.get("score", 0.0) for item in results) / len(results)

    # 基础置信度 = 平均评分
    confidence = avg_score

    # 多源加成（更重要）
    if multi_source:
        confidence = min(1.0, confidence + 0.15)  # 提升权重

    # 官方确认加成（降低权重）
    if official_confirmed:
        confidence = min(1.0, confidence + 0.10)  # 从 0.15 降到 0.10

    # 结果数量加成
    if len(results) >= 5:
        confidence = min(1.0, confidence + 0.05)

    return round(confidence, 2)
```

---

### 3. 强制域名白名单

对高优先级事件类型，强制限制域名：

```python
# 在 GeminiDeepAnalysisEngine 或配置中定义
HIGH_PRIORITY_EVENT_DOMAINS = {
    "hack": ["coindesk.com", "theblock.co", "cointelegraph.com", "decrypt.co"],
    "regulation": ["coindesk.com", "theblock.co", "theblockcrypto.com"],
    "listing": ["coindesk.com", "theblock.co", "cointelegraph.com"],
    "partnership": ["coindesk.com", "theblock.co"],
}

# 在 _execute_search_tool 中应用
def _execute_search_tool(self, state: DeepAnalysisState) -> Optional[dict]:
    preliminary = state["preliminary"]
    event_type = preliminary.event_type

    # 强制域名限制
    include_domains = HIGH_PRIORITY_EVENT_DOMAINS.get(event_type)

    result = await self._search_tool.fetch(
        keyword=keyword,
        max_results=5,
        include_domains=include_domains  # 🆕 传递域名白名单
    )
```

---

### 4. SearchTool 接口扩展

更新 `SearchTool.fetch()` 支持域名参数：

```python
async def fetch(
    self,
    *,
    keyword: str,
    max_results: Optional[int] = None,
    include_domains: Optional[list[str]] = None  # 🆕 新增参数
) -> ToolResult:
    target = max_results or self._max_results
    return await self._provider.search(
        keyword=keyword,
        max_results=target,
        include_domains=include_domains  # 传递到 Provider
    )
```

---

## 📝 文档待更新

需要在 `phase1_search_tool_implementation_cn.md` 中更新：

1. **任务 1.3** - `TavilySearchProvider.search()` 签名添加 `include_domains` 参数
2. **任务 3.4** - `_execute_search_tool` 根据事件类型传递域名白名单
3. **配置章节** - 添加 `HIGH_PRIORITY_EVENT_DOMAINS` 配置示例
4. **成本章节** - 确认实际延迟 1.5-2.5s（符合预期）

---

## ✅ 验证结论

| 验证项 | 状态 | 备注 |
|--------|------|------|
| API 连接正常 | ✅ | 所有请求 200 OK |
| 响应时间 | ✅ | 1.5-2.5s，符合预期 |
| 域名过滤 | ✅ | `include_domains` 严格生效 |
| 评分系统 | ✅ | 0.5-0.7 范围合理 |
| 中英文查询 | ✅ | 完全支持 |
| 官方关键词 | ⚠️ | 检测率低，不可作为唯一依据 |
| 结果质量 | ✅ | 限制域名后质量稳定 |

**总结**: Tavily API **完全可用**，响应格式与文档描述一致，可直接进入实施阶段。

---

## 🔗 相关文件

- 测试脚本: `scripts/test_tavily_api.py`
- 实施计划: `docs/phase1_search_tool_implementation_cn.md`
- 配置示例: `.env`
