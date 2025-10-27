# AI 信号引擎总览

## 概述
- AI 信号引擎负责将 Telegram 快讯转化为结构化交易信号，结合关键词过滤、去重、记忆检索与双引擎 AI 分析，最终输出可执行动作。
- MVP 聚焦上所/下架、黑客、监管、融资、巨鲸、清算等核心事件，逐步扩展到合作集成、治理、宏观、名人言论等场景。
- 系统兼容无 AI 模式，降级后保持原始转发，确保 listener 主流程稳定。

## 目标
- 提供秒级响应的结构化信号，支持热路径下单与风控。
- 抽象 AI 模型接口，支持 Gemini、Claude 等多模型切换与并行实验。
- 构建事件本体化与行动矩阵，统一置信度、强度、方向、风险标签输出。
- 在成本可控前提下实现逐步上线（观察 → 小仓位 → 加仓），并提供监控与日志。

## 架构与流程
- **总体流程**：
  ```
  TG Source → Listener → 去重 (内存/哈希/语义) → 翻译 → 关键词/记忆
       → AiSignalEngine (Gemini 主引擎 + 可选深度分析)
       → 决策 (置信度/行动矩阵)
       → 转发 & 交易执行 → Supabase 持久化
  ```
- **LangGraph（实验管道）节点**：
  - `ingest`：解析 Telegram 元数据。
  - `keyword_filter`：关键词过滤 + 优先 KOL 通道。
  - `dedup_memory/hash/semantic`：三层去重。
  - `translation`：多供应商翻译；可选跳过。
  - `memory_fetch`：Hybrid 记忆检索，注入历史案例。
  - `ai_signal`：双引擎分析，输出 `SignalResult`。
  - `forward`：根据置信度阈值控制转发与热路径执行。
  - `persistence`：写入 `news_events` 与 `ai_signals`。
- **模块结构**：
  ```
  src/ai/
    signal_engine.py     # AiSignalEngine 主类
    gemini_client.py     # Gemini API 封装
    prompts.py           # 提示模板
    __init__.py
  ```
- **事件本体化 & 行动矩阵**：
  - 事件类型：listing/delisting/hack/regulation/funding/whale/liquidation/partnership/product_launch/governance/macro/celebrity/airdrop/other。
  - 行动指令：buy/sell/observe，配合方向 long/short/neutral 与强度 low/medium/high。
  - 风控：金额不足、缺乏官方确认时自动降置信度并设为观望。

## 实施步骤
- **GeminiClient**：
  - 使用 `google-genai` 初始化 `genai.Client`，暴露 `async generate_signal(...)`。
  - 处理超时、429、重试（指数退避），异常包装为 `AiServiceError`。
- **AiSignalEngine**：
  - 通过配置构造 `AiSignalEngine(client, threshold, semaphore)`。
  - `analyse()` 在信号关闭或初始化失败时自动降级；成功时解析 JSON、封装 `SignalResult`。
  - 支持热路径与日志输出，默认在 listener 中串行调用以满足速率限制。
- **提示模板** (`build_signal_prompt`)：
  - 输入：原文、译文、来源、时间、事件哈希、关键词、历史参考、市场快照等。
  - 输出要求严格 JSON，无 Markdown 包裹；包含 summary、event_type、asset、action、direction、confidence、strength、risk_flags、notes。
  - 提供 few-shot 示例，包含金额、官方确认等约束。
- **Listener 集成**：
  - `_handle_new_message` 构造 `EventPayload`，调用 `ai_engine.analyse()`。
  - 根据 `SignalResult` 决定转发、热路径执行、附加备注。
  - `_persist_event` 写入 Supabase；保留原始消息、AI 标签、置信度。
- **数据落地与执行**：
  - `src/storage/supabase_client.py` 或现有数据层新增写入逻辑。
  - 交易执行器根据 `should_execute_hot_path` 下单，并记录结果。
- **渐进式上线**：
  1. 观测模式：仅生成摘要和标签，人工验证。
  2. 小仓位实盘：开启热路径但限制仓位。
  3. 完整模式：叠加盘口确认、风控熔断。

## 配置
- 核心开关与参数：
  ```bash
  AI_ENABLED=true
  GEMINI_API_KEY=...
  AI_MODEL_NAME=gemini-2.5-flash-lite
  AI_SIGNAL_THRESHOLD=0.4
  AI_TIMEOUT_SECONDS=8
  AI_MAX_CONCURRENCY=3
  AI_RETRY_ATTEMPTS=1
  AI_RETRY_BACKOFF_SECONDS=2
  ```
- 深度分析联动：
  - `DEEP_ANALYSIS_ENABLED`、`DEEP_ANALYSIS_MIN_INTERVAL` 控制高价值升级。
  - 优先 KOL 配置：`PRIORITY_KOL_FORCE_FORWARD=true` 与降级阈值。
- LangGraph 实验开关：`USE_LANGGRAPH_PIPELINE=true`。
- 翻译与记忆：
  ```bash
  TRANSLATION_PROVIDERS=deepl,azure
  MEMORY_ENABLED=true
  MEMORY_BACKEND=hybrid
  ```

## 验证与测试
- 单元测试：
  - `FakeGeminiClient` 覆盖正常/异常路径。
  - `parse_signal_response` 针对字段缺失、格式错误的校验。
- 集成测试：
  - 模拟 Telegram 消息流，断言 listener 写库与转发行为。
  - LangGraph 节点级测试验证去重、记忆注入、工具规划。
- 手动回归：
  - 启动 listener：`uvx --with-requirements requirements.txt python -m src.listener`
  - 观察日志中的 `AI 调用`、`SignalResult`、`热路径执行` 记录。

## 里程碑与状态
- 产品愿景、事件范围、LangGraph 流程与节点说明已定稿。
- `AiSignalEngine`、`GeminiClient`、提示模板和配置项落地，支持降级与重试。
- 正在推进搜索工具、记忆注入与热路径执行的灰度上线。

## 风险与后续
- API 成本与速率：需监控调用次数，结合配额策略与缓存减少冗余请求。
- JSON 解析失败：保持严格提示模板与单测，出现异常时快速降级。
- 多模型演进：后续引入 Claude/OpenAI A/B，需要抽象模型路由。
- 风控：持续调整 `AI_SIGNAL_THRESHOLD`、白名单策略、熔断参数。
- 下一步：完善行动矩阵回测、接入价格/盘口轻确认、扩展监控仪表盘与指标输出。
