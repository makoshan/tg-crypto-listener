# Source Prioritization Plan (Prompt-Only Edition)

目的：在保持现有数据结构不变的前提下，仅通过 **AI 提示词与现有字段（source、正文关键字）** 来提升三类来源的信号权重，并配合轻量的 listener 调整（不引入额外标签机制）。

---

## 总体流程回顾
1. `listener.py` 拉取消息 → 关键词/去重过滤 → 构造 `EventPayload`。
2. `EventPayload` 序列化进入 `build_signal_prompt`，AI 输出交易信号。
3. 信号存入数据库/记忆系统，供后续参考。

计划是在关键词命中后，通过：
1. **system prompt / user prompt** 中新增来源识别指令；
2. `listener.py` 里以 `source` 或正文关键字做最小化差异化处理；
3. 记忆写入时复用原有字段；
来实现优先级控制。

---

## 任务 1：@mlmonchain（Hyperliquid 链上情报）

**目标**  
将 Hyperliquid 巨鲸（内幕哥、神秘姐、临时加仓等）相关的消息视为一类高权重信号，强化即时提示与历史记忆。

**实现要点**
- **Prompt 强化**  
  - 在 `build_signal_prompt` 的 system prompt 中新增段落：当传入 AI 的 `context` 中 `source` 明确为 `@mlmonchain`，或正文命中 “内幕哥 / 神秘姐 / 巨鲸 / 杠杆”等关键词时，应将其视为 Hyperliquid 巨鲸情报，并要求模型：
    1. 提升 `strength` 至至少 `medium`，除非明确无执行价值；
    2. 在 `summary/notes` 中描述仓位方向、杠杆或资金规模；
    3. 若缺乏关键数据，仍需解释缺失项并给出跟踪条件。
- **记忆强化**  
  - 保存历史信号时无需新字段，只需在 `summary/notes` 中保留 “Hyperliquid 巨鲸” 关键词，便于现有记忆功能按文本相似度召回。
- **listener 调整（可选）**  
  - 针对 `source == "@mlmonchain"` 的消息放宽去重阈值或直接略过关键词过滤，确保该类消息不会在前置过滤阶段被拦截，从而能进入 AI 并触发上方的 prompt 指令。

---

## 任务 2：@marketfeed（宏观信息量大）

**目标**  
保留有价值的宏观信号，过滤重复或与加密无关的噪音。

**实现要点**
- **关键词 + 速率控制**  
  - 在 `listener.py` 中仅针对 `source == "@marketfeed"` 实施：  
    1. `MARKETFEED_KEYWORDS`（宏观 + 美国政府相关词，如 ETF、CPI、财政部、收益率、联储、BTC/ETH）。  
    2. 若正文不包含关键词则直接存档不送 AI；包含则放行。  
    3. 维护 `(topic, timestamp)` 字典以限制重复主题（如 10 分钟内重复的 “美国 CPI”）。
- **Prompt 指南**  
  - system prompt 中加入段落：当 `source="@marketfeed"` 时，模型需判断宏观数据对加密资产的传导链。  
    - 若有明确影响，给出 actionable 建议；  
    - 若传导链不足，输出 `observe` 并说明缺失的信息（如“尚未观察到链上资金流”）。

---

## 任务 3：重点 KOL（@SleepinRain / @journey_of_someone）

**目标**  
确保这两位 KOL 信号不被过滤或降权，同时保留风险说明。

**实现要点**
- **过滤豁免**  
  - 在 `listener.py` 中对 `source in {"@SleepinRain", "@journey_of_someone"}` 的消息直接跳过关键词过滤和大部分去重逻辑。
- **Prompt 指引**  
  - 在 system prompt 中增加指示：  
    - 当 `source` 为以上 KOL 时，模型必须保留其核心观点，输出明确的执行条件、监控指标和风险对冲建议；  
    - 即使无法即刻执行，也要解释观望原因，避免被忽略。
- **转发逻辑调整**  
  - 在 AI 结果处理阶段，当 `source` 为重点 KOL 时，不因 `observe` 或低置信度自动跳过转发，notes 中强调执行门槛。

---

## 配置与代码改动摘要
1. `.env` / `config.py`：定义关键词与控制参数（可复用现有机制），例如  
   - `HYPERLIQUID_KEYWORDS="Hyperliquid,HYPE,巨鲸,杠杆,多单,空单,加仓,perp"`（不限于 @mlmonchain，任何来源命中这些词都触发 Hyperliquid 情报处理）  
   - `MARKETFEED_KEYWORDS="ETF,CPI,财政部,收益率,联储,BTC,ETH"`  
   - `MARKETFEED_TOPIC_WINDOW_SECONDS=600`
2. `listener.py`：  
   - 针对 `@mlmonchain`、`@marketfeed`、两位 KOL 的分支逻辑（关键词/速率控制/跳过过滤）。  
   - 记录必要的主题时间戳缓存。
3. `build_signal_prompt`：  
   - 在 system prompt 中添加基于 `source` 和正文关键词的优先级说明。  
   - 可在 `context` 中明确 `source`，方便 prompt 引导。
4. 记忆与持久化：复用原有字段，无需 schema 变更，只需确保 `summary/notes` 中保留来源关键字以便检索。

---

## 可行性检查
- **过滤阶段**：`listener.py` 在 `contains_keywords(message_text, self.config.FILTER_KEYWORDS)` 判定未命中时直接返回。若 @mlmonchain、重点 KOL 或 Hyperliquid 关键词未加入全局关键词集合，则会在进入 AI 前被丢弃，需在 listener 中为这些来源设置白名单或扩充关键词。  
- **来源格式**：`source_name` 基于频道标题或用户名（`listener.py:312-319`），并非固定 `@handle`。实现时需将 `channel_username.lower()` 写入 `EventPayload`，以便 prompt 中的 `source == "mlmonchain"` 等条件生效。  
- **配置解析**：当前 `Config` 仅解析 `FILTER_KEYWORDS`。文档中新增的 `HYPERLIQUID_KEYWORDS`、`MARKETFEED_KEYWORDS`、速率窗口、优先账号等需要在 `config.py` 落地解析与默认值。  
- **Prompt 生效范围**：`build_signal_prompt` 已注入 `source`、原文、翻译等上下文，因此补充系统提示后模型能识别来源差异，但前提仍是消息通过 listener 过滤。  
- **记忆召回**：记忆系统按文本相似度工作（`listener.py:480-539`），只要 `summary/notes` 包含关键字即可在后续事件中被引用，无需额外字段。

---

## 实现伪代码示例

```python
# config.py
class Config:
    ...
    HYPERLIQUID_KEYWORDS = {
        kw.strip().lower()
        for kw in os.getenv(
            "HYPERLIQUID_KEYWORDS",
            "hyperliquid,hype,巨鲸,杠杆,多单,空单,加仓,perp"
        ).split(",")
        if kw.strip()
    }
    MARKETFEED_KEYWORDS = {
        kw.strip().lower()
        for kw in os.getenv(
            "MARKETFEED_KEYWORDS",
            "etf,cpi,财政部,收益率,联储,btc,eth"
        ).split(",")
        if kw.strip()
    }
    MARKETFEED_TOPIC_WINDOW_SECONDS = int(
        os.getenv("MARKETFEED_TOPIC_WINDOW_SECONDS", "600")
    )
    PRIORITY_KOL_HANDLES = {
        handle.strip().lower()
        for handle in os.getenv(
            "PRIORITY_KOL_HANDLES",
            "sleepinrain,journey_of_someone"
        ).split(",")
        if handle.strip()
    }
    PRIORITY_CHANNELS = {
        handle.strip().lower()
        for handle in os.getenv("PRIORITY_CHANNELS", "mlmonchain").split(",")
        if handle.strip()
    }
```

```python
# listener.py
channel_username = (getattr(source_chat, "username", "") or "").lower()
is_priority_kol = channel_username in self.config.PRIORITY_KOL_HANDLES
is_hyperliquid_channel = channel_username in self.config.PRIORITY_CHANNELS
text_hits_hyperliquid = contains_keywords(
    message_text, self.config.HYPERLIQUID_KEYWORDS
)

if not (
    is_priority_kol
    or is_hyperliquid_channel
    or text_hits_hyperliquid
    or contains_keywords(message_text, self.config.FILTER_KEYWORDS)
):
    self.stats["filtered_out"] += 1
    return

if channel_username == "marketfeed":
    topic = extract_macro_topic(message_text)
    if not contains_keywords(message_text, self.config.MARKETFEED_KEYWORDS):
        persist_without_ai(...)
        return
    if self._topic_limiter.is_within_window(topic):
        persist_without_ai(...)
        return
    self._topic_limiter.mark(topic)

if is_priority_kol:
    skip_dedup = True
elif is_hyperliquid_channel or text_hits_hyperliquid:
    adjust_dedup_threshold(...)
else:
    skip_dedup = False

if not skip_dedup and self.deduplicator.is_duplicate(message_text):
    ...
```

```python
# signal_engine.py
system_prompt += """
## 来源特性处理
- source 为 "mlmonchain" 或正文命中 HYPERLIQUID_KEYWORDS → 视为 Hyperliquid 巨鲸情报，
  strength 至少 medium；notes 需写明仓位方向、杠杆、资金规模或缺失说明。
- source 为 "marketfeed" → 判断宏观指标与加密市场的传导链；若路径不明，输出 observe 并指出待验证数据。
- source 属于 SleepinRain / journey_of_someone → 保留核心观点，给执行条件、监测指标与对冲方案。
"""
```

```python
# 主题速率控制辅助类
class TopicRateLimiter:
    def __init__(self, window_seconds: int):
        self.window = timedelta(seconds=window_seconds)
        self.cache: dict[str, datetime] = {}

    def is_within_window(self, topic: str) -> bool:
        last = self.cache.get(topic)
        return bool(last and datetime.utcnow() - last < self.window)

    def mark(self, topic: str) -> None:
        self.cache[topic] = datetime.utcnow()
        cutoff = datetime.utcnow() - self.window
        self.cache = {k: t for k, t in self.cache.items() if t >= cutoff}
```

---

## 验收建议
- **单测**：构造三类来源的模拟消息，验证 listener 的分支逻辑与 prompt 指引是否触发（例如 strength/notes 是否符合预期）。  
- **集成测试**：运行 listener stub，确认 @mlmonchain/@marketfeed/KOL 的消息在无新字段情况下仍能被 AI 正确识别。  
- **上线后观察**：关注 AI 输出中的 `notes`、`strength`、`confidence`，按需调整关键词、速率窗口与 prompt 描述。
