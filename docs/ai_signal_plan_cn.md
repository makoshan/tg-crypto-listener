# AI 信号方案（MVP + 演进）

## 1. 产品愿景
- 一句话愿景：用 AI 和工程化方法，把币圈新闻快讯转化为结构化信号，实现秒级下单，获取超额收益。
- 核心价值主张：
  - 比市场快半步（信息差）；
  - 将复杂新闻转成明确行动（Action Matrix）；
  - 风控兜底（小仓试错、逐步加仓）。

## 2. 核心用户场景
1. 个人 / 小团队交易者：监听 Telegram 新闻，自动触发交易，减少人工反应时间。
2. 研究者 / 分析员：沉淀事件与价格影响的数据，回测历史胜率指导仓位策略。
3. 扩展愿景：接入更多高频信息源，演化为“加密金融信息处理引擎”。

## 3. 方法论基线
- 第一性原理：
  - 海耶克知识分散观：快速获取局部信息即 Alpha 来源；
  - 信息经济学：专注高价值新闻，控制处理成本；
  - 行为金融：市场反应滞后/过度，存在短期套利窗口。
- 事件本体化（Event Ontology）：
- MVP：围绕上所/下架、黑客、监管、融资、巨鲸、清算等核心快讯事件；
- 扩展：补充合作集成、产品发布、治理提案、宏观动向、名人言论、空投激励等场景，使用行动矩阵映射交易指令；
  - 进阶：叠加轻确认层（盘口/成交量），最终演进为多模态权重。

## 4. 成熟度范围（Scope）
- In Scope（MVP）：
  - Telethon 抓取 Telegram 源；
  - 新闻翻译（可选）+ 事件本体化；
  - 行动矩阵映射交易动作或提醒；
  - Supabase 存储事件 / 行情快照 / 交易记录；
  - 风控：资金上限、熔断、日志、报警。
- Out of Scope（后续）：
  - 多模态数据融合（链上、预测市场、宏观）；
  - 自适应权重 / 强化学习；
  - Token 激励或外部用户产品。

## 5. 系统架构概览
```
[TG Source Channels]
   │  Telethon Listener (NewMessage)
   ▼
[Ingest Pipeline]
   - 去重 (hash)
   - 翻译 (LLM / API)
   - 结构化 JSON (事件类型 / 资产 / 强度 / 时间)
   ▼
[Decision Engine]
   - Action Matrix (Ontology 映射表)
   - 热路径 (≤5s 小仓位下单)
   - 温路径 (30–120s 确认后加仓/撤销)
   ▼
[Trade Executor]
   - API 下单 (CEX/DEX)
   - 止盈/止损自动设置
   ▼
[Storage: Supabase]
   - events / trades / market_snapshots / metrics_daily
   ▼
[Notifier]
   - 推送至 TG 私人频道 (原文+翻译+执行动作)
```

## 6. AI 集成方案
1. **AI 处理节点**：在 `src/listener.py:137`–`158` 的过滤与去重后加入 `AiSignalEngine`，输入原文、来源、时间戳等元数据。
2. **Gemini 客户端**：
   - 新建 `src/ai/gemini_client.py` 封装 `google-genai`，提供异步接口（`asyncio.to_thread` 或 `httpx.AsyncClient`）。
   - 统一配置模型（`gemini-2.5-flash`/`gemini-2.5-pro`）、温度、重试和超时。
3. **提示模板**：在 `src/ai/prompts.py` 设计严格的 JSON 输出提示，包含：
   - **输入字段**：原文、译文、来源、时间戳、事件哈希、资产代码、金额规模、`historical_reference`（近 1h 成交量/资金流均值）、关键词命中列表。
 - **输出格式**：要求模型仅返回 JSON 字符串，不得加 `json` 前缀或 Markdown 代码块。字段定义：
     ```json
     {
      "summary": "一句话中文结论",
      "event_type": "listing|delisting|hack|regulation|funding|whale|liquidation|partnership|product_launch|governance|macro|celebrity|airdrop|other",
      "asset": "受影响的币种代码，多个用逗号分隔",
      "action": "buy|sell|observe",
      "direction": "long|short|neutral",
       "confidence": 0.0,
       "strength": "low|medium|high",
       "risk_flags": [""],
       "notes": "补充说明(可为空字符串)"
     }
     ```
   - **约束补充**：
     1. 金额 < 3000 美元或缺少关键信息 → 强制 `action="observe"`、`confidence ≤ 0.3`、`strength="low"`。
     2. `liquidation`、`whale` 场景需对比 `historical_reference` 判断规模偏离度，并在 `notes` 中说明理由。
     3. 所有文本使用简体中文，不得返回额外解释、emoji 或多语言内容。
  - **Few-shot 示例**：提供“巨鲸转入”“黑客攻击”“大额清算”三类样例，分别展示高置信度与低置信度下 `action`、`notes` 的写法，帮助模型对齐交易强度。
  - **字段规范**：
      - `risk_flags` 仅允许从枚举列表中选择：`price_volatility`、`liquidity_risk`、`regulation_risk`、`confidence_low`、`data_incomplete`，无风险则返回空数组。
      - `confidence` 建议输出 0–1 之间、保留两位小数，内部提示模型结合金额、历史偏离度及事件类型映射分级。
      - `strength` 与 `confidence` 映射关系：`confidence < 0.3 -> low`，`0.3 ≤ confidence < 0.7 -> medium`，`≥ 0.7 -> high`，除非特殊说明。
      - `notes` 应简洁解释驱动因素，若模型判定为观察态，则需说明“金额小/信息不足/影响不确定”等原因。
   - **多语场景支持**：提示词需明确：
       1. 将原始文本（含中/英/韩等语言）统一翻译为简体中文后再做判断，可在输入中提供 `translated_text` 字段，缺失时要求模型先内联翻译。
       2. 对“交易提案”“政策监管”“关键人物言论”“宏观/美股/地缘政治”等潜在可赚钱信号进行归类，并在 `notes` 中说明潜在交易机会或关注点。
       3. 若提示中出现多个事件，请逐条（按换行符或编号）分析并合并为一份总结，确保不会遗漏潜在 Alpha。必要时可在 `notes` 中列出 `signal_items`。
       4. 允许模型根据上下文判断是否属于长线主题（如 AI、RWA、L2、宏观流动性），并在 `risk_flags` 中添加 `data_incomplete` 或 `regulation_risk` 等提醒。

4. **多语言翻译模块**：在 AI 调用前新增翻译流程：
   - 新建 `src/ai/translator.py`，封装第三方翻译（DeepL、OpenAI、Gemini translation 或自建词表），确保支持中/英/韩/日等常见语种自动转中文。
   - 对短文本可直接调用 LLM 快速翻译；长文本采用批量切分+缓存策略，保证低延迟。
   - 翻译模块输出 `translated_text`、`language_detected`、`confidence`，供 `AiSignalEngine` 决定是否继续使用原文。
   - 当翻译失败时回退到原文，让模型自行翻译，并在 `risk_flags` 增加 `data_incomplete`。

### 通知格式（现行）
- 顶部展示 `⚡ 信号摘要`，由翻译文本与 AI 摘要拼接成一句话，直接说明事件与建议。
- `🎯 操作要点` 只保留核心字段：标的、动作（含方向）、置信度 ± 强度、风险提示、备注（可选）。
- `📡 来源` 与 `🕒 时间` 紧随其后，保证元数据统一。
- 原文仅在以下条件展示：
  1. AI 识别不到有效币种；
  2. 置信度 < 0.4；
  3. 风险提示包含 `data_incomplete` 或 `confidence_low`；
  4. 备注中要求“查看原文”。
  其余情况默认隐藏，保持通知简洁。

4. **配置扩展**：`Config` 增加 `GEMINI_API_KEY`、`AI_MODEL_NAME`、`AI_SIGNAL_THRESHOLD`、`AI_ENABLED` 等字段，缺少凭证时降级为纯转发。
5. **信号解析**：解析 Gemini JSON 为 `SignalResult`，包括方向、置信度、风险提示。
6. **输出融合**：
   - 热路径：置信度 ≥ 阈值时触发小仓位下单，并在 TG 提示中加入行动摘要。
   - 温路径：记录待确认信号，在 30–120s 内结合盘口指标再决定加仓/撤销。
7. **守护与容错**：AI 调用异常或超时则回退到原有流程，记录错误日志，保证监听不被阻塞。

## 7. 事件 → 行动矩阵（MVP）
| 事件类型 | 方向 | 强度 | 热路径（≤5s） | 温路径确认（30–120s） | 风控 |
| --- | --- | --- | --- | --- | --- |
| 交易所上架（大所） | 多 | 强 | 小仓市价买入 (0.5%) | 成交量 >3x 且买单占比↑ → 加仓至 2% | 止损 -1.5%，止盈 +3% |
| 黑客 / 漏洞 / 停机 | 空 | 强 | 小仓做空/减仓 | 资金费率转负 & OI↑ → 加空 | 反抽 +1.2% 平仓 |
| 监管利空 | 空 | 中 | 小仓空或减仓 | BTC/ETH 同步下跌 → 加空 | 日内回撤阈值 |
| 融资 / 合作 / 主网 | 多 | 中 | 推送提示 | 链上资金流 + 成交量共振 → 小仓买入 | 止损 -1% |
| 巨鲸转入交易所 | 空 | 中 | 提示观察 | 1h 净流入 >5000万 & OI↑ → 小仓空 | 无效信号撤销 |

## 8. 数据模型（Supabase）
- `events`：`{id, ts, source, type, asset, direction, strength, confidence, raw, translated, hash}`
- `trades`：`{event_id, side, size, price, sl, tp, status}`
- `market_snapshots`：价格、成交量、盘口、资金费率等指标
- `metrics_daily`：收益、回撤、胜率等聚合数据

## 9. 风控与监控
- 资金闸门：单事件 ≤0.5–2%，单日最大回撤 ≤3%，总仓位 ≤10%。
- 熔断：连续 3 条信号亏损 → 停止交易，仅推送提示。
- 审计日志：事件 → Action → Trade → PnL 全链路可回放。
- 报警：下单失败、数据缺失、延迟超阈值通过 TG 通知。

## 10. 迭代里程碑
- Week 1：事件 Schema、Telethon 抓取、翻译模块 → Supabase 入库。
- Week 2：行动矩阵 MVP（上所/黑客/巨鲸）→ 回测 5m/30m。
- Week 3：接入交易 API，打通热/温路径，落地风控日志。
- Week 4：小额实盘灰度、TG 通知、监控/报警上线。

## 11. 演进路线（V2+）
- 多源扩展：Twitter / RSS / 链上数据 / 预测市场。
- 确认升级：多模态共振（新闻 + 盘口 + 链上）。
- 模型增强：从固定行动表升级到动态权重（统计或强化学习）。
- 风险优化：引入 CVaR、最大回撤约束。
- 产品化：对外提供 TG Bot、Dashboard 或 API 接口。

## 12. 价值总结
- 短期（MVP）：打造低延迟、可回放的新闻交易闭环，抢占快信息带来的收益。
- 中期：用数据验证并迭代行动矩阵，提升胜率和资金效率。
- 长期：构建“信息驱动金融引擎”，可扩展到预测市场、RWA、AI Agent 等领域。

## 13. 现有技术流程
- Telethon 监听新消息 → 去重 → （可选）翻译 → AI 结构化 → 推送至 TG → 同步写入 Supabase。
- 依赖：`telethon==1.36.0`、`python-dotenv>=1.0.0`、`requests>=2.31.0`（后续添加 `google-genai`、`httpx` 等）。
- 环境：`python3 -m venv .venv`，使用 pnpm + PM2 做守护/日志/自启。

## 14. 下一步实施建议
1. 搭建 `AiSignalEngine` 骨架（返回模拟信号）并接入监听器，验证消息格式。
2. 增加 Gemini 客户端与提示模板，跑小批量样本评估输出质量，设定阈值。
3. 上线配置开关与速率限制，结合 Supabase 落地事件/交易记录，完成风控监控。
