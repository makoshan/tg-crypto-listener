# 信号级别去重 (Signal-Level Deduplication)

## 概述

信号级别去重是在现有的消息去重（内存、哈希、语义）基础上,增加的第四层去重机制,专门用于检测和过滤**AI 生成的重复交易信号**。

## 问题背景

### 现有去重机制的局限

tg-crypto-listener 已有三层消息去重:

1. **内存去重**: `MessageDeduplicator` 检查最近 N 小时的消息文本
2. **哈希去重**: `compute_sha256(text)` 检查数据库中的完全相同文本
3. **语义去重**: `embedding` + PostgreSQL RPC 检查语义相似的事件

这些机制能有效防止**原始消息**重复,但无法处理以下场景:

### 实际案例

```
来源消息 1: "Trump cancels Canada trade talks" (华盛顿邮报)
来源消息 2: "Trump ends Canada trade negotiations" (unusual_whales Twitter)

→ 原始文本不同,通过了所有消息级别去重
→ AI 分析后生成几乎相同的信号:

⚡ 信号 1
EWCL NEWS: 特朗普宣布取消与加拿大贸易谈判,此举可能加剧地缘政治紧张...
操作: BTC,ETH,SOL,卖出,做空,置信度 0.85

⚡ 信号 2
EWCL NEWS: 特朗普总统宣布与加拿大结束贸易谈判,加剧了地缘政治不确定性...
操作: BTC,ETH,SOL,卖出,做空,置信度 0.85

→ 用户看到两条几乎相同的信号 ❌
```

### 根本原因

- **不同来源报道同一事件** → 原始文本不同 → 绕过消息去重
- **AI 摘要措辞略有差异** → 语义相似但哈希不同
- **元数据完全一致** → 同样的 asset/action/event_type/confidence

## 解决方案

### 核心思路

在 **AI 分析后、消息转发前** 增加信号级别的相似度检测:

```
新闻事件 → AI 分析 → 生成信号
                          ↓
                    【信号去重检查】
                          ↓
                   是否与近期信号相似?
                    /              \
                  是                否
                   ↓                ↓
              跳过转发          转发到频道
```

### 实现架构

#### 1. `SignalMessageDeduplicator` 类

**位置**: `src/utils.py:176-277`

**核心算法**:
```python
def is_duplicate(summary, action, direction, event_type, asset, asset_names) -> bool:
    # 1. 归一化摘要文本
    normalized_summary = _normalize_text(summary)

    # 2. 元数据归一化
    metadata = _normalize_metadata(action, direction, event_type, asset, asset_names)

    # 3. 遍历时间窗口内的历史信号
    for entry in recent_entries:
        # 3.1 元数据必须完全匹配
        if entry.metadata != metadata:
            continue

        # 3.2 文本相似度检查 (SequenceMatcher)
        similarity = SequenceMatcher(None, normalized_summary, entry.summary).ratio()
        if similarity < threshold:  # 默认 0.68
            continue

        # 3.3 字符集重叠验证
        common_chars = len(char_set & entry.char_set)
        if common_chars < min_common_chars:  # 默认 10
            continue

        # 检测到重复
        return True

    # 添加到历史记录
    entries.append(new_entry)
    return False
```

#### 2. 文本归一化策略

**目的**: 移除动态内容,保留核心语义

```python
def _normalize_text(text: str) -> str:
    # 1. Unicode 规范化
    text = unicodedata.normalize("NFKC", text)

    # 2. 转小写
    text = text.lower()

    # 3. 移除 URL
    text = re.sub(r"https?://\S+", "", text)

    # 4. 移除数字 (价格、时间)
    text = re.sub(r"[0-9]+(?:\.[0-9]+)?", "", text)

    # 5. 移除标点符号
    text = re.sub(r"[，,。.!？?：:；;\"'""''()（）\[\]{}<>《》•—\-·…~`_]+", "", text)

    # 6. 移除空白
    text = re.sub(r"\s+", "", text)

    return text
```

**示例**:
```python
原文1: "BTC 价格上涨至 $110,979.53,涨幅 2.18%。https://example.com/1"
原文2: "BTC 价格上涨至 $111,217.47,涨幅 2.21%。https://example.com/2"

归一化后: "btc价格上涨至涨幅"

→ 完全相同,检测为重复 ✅
```

#### 3. 元数据匹配

**字段**:
- `action`: 买入/卖出/观察
- `direction`: 做多/做空/中性
- `event_type`: listing/hack/regulation 等
- `asset`: BTC,ETH,SOL
- `asset_names`: 比特币,以太坊,索拉纳

**匹配策略**:
- 大小写不敏感 (`action.lower()`)
- Unicode 规范化
- 必须**完全匹配**才继续检查文本相似度

**原因**: 相同事件可能对不同资产产生不同信号,不应误判为重复

#### 4. 相似度阈值

**文本相似度** (`SequenceMatcher.ratio()`):
- 默认: **0.68**
- 范围: 0.0 (完全不同) ~ 1.0 (完全相同)
- 实测示例:
  ```
  "特朗普宣布取消与加拿大贸易谈判,此举可能加剧..."
  "特朗普总统宣布与加拿大结束贸易谈判,加剧了..."
  → 相似度: 0.698 ✅ 检测为重复
  ```

**字符集重叠**:
- 默认: **10 个公共字符**
- 防止误判短文本或结构相似但内容不同的信号

#### 5. 时间窗口

- 默认: **360 分钟 (6 小时)**
- 自动清理过期条目
- 每次检查时触发清理逻辑

## 配置选项

### 环境变量

```bash
# 是否启用信号去重 (默认: true)
SIGNAL_DEDUP_ENABLED=true

# 时间窗口 (分钟,默认: 360 = 6小时)
SIGNAL_DEDUP_WINDOW_MINUTES=360

# 文本相似度阈值 (0.0-1.0,默认: 0.68)
SIGNAL_DEDUP_SIMILARITY=0.68

# 最小公共字符数 (默认: 10)
SIGNAL_DEDUP_MIN_COMMON_CHARS=10
```

### 调优建议

**提高阈值 (减少误报,可能漏检)**:
```bash
SIGNAL_DEDUP_SIMILARITY=0.75  # 要求更高相似度
SIGNAL_DEDUP_MIN_COMMON_CHARS=20  # 要求更多公共字符
```

**降低阈值 (减少漏检,可能误报)**:
```bash
SIGNAL_DEDUP_SIMILARITY=0.60  # 允许更低相似度
SIGNAL_DEDUP_MIN_COMMON_CHARS=5  # 允许更少公共字符
```

**延长窗口 (防止长时间重复)**:
```bash
SIGNAL_DEDUP_WINDOW_MINUTES=1440  # 24小时
```

## 集成点

### 1. 传统监听器 (src/listener.py:773-788)

```python
if self.signal_deduplicator and ai_kwargs.get("ai_summary"):
    if self.signal_deduplicator.is_duplicate(
        summary=str(ai_kwargs.get("ai_summary") or ""),
        action=str(ai_kwargs.get("ai_action") or ""),
        direction=str(ai_kwargs.get("ai_direction") or ""),
        event_type=str(ai_kwargs.get("ai_event_type") or ""),
        asset=str(ai_kwargs.get("ai_asset") or ""),
        asset_names=str(ai_kwargs.get("ai_asset_names") or ""),
    ):
        self.stats["duplicates"] += 1
        self.stats["dup_signal"] += 1
        logger.info("🔄 信号内容与近期重复,跳过转发: source=%s", source_name)
        return  # 跳过转发
```

**时机**: 在 `build_ai_kwargs()` 后、`format_forwarded_message()` 前

### 2. LangGraph Pipeline (src/pipeline/langgraph_pipeline.py:872-894)

```python
# Signal-level deduplication check
if deps.signal_deduplicator and ai_kwargs.get("ai_summary"):
    is_dup = deps.signal_deduplicator.is_duplicate(...)
    if is_dup:
        deps.stats["duplicates"] += 1
        deps.stats["dup_signal"] += 1
        routing.forwarded = False
        routing.drop_reason = "duplicate_signal"
        return {"control": control, "routing": routing}
```

**位置**: `_node_forward` 方法,在构建消息前

## 统计与监控

### 新增统计项

```python
self.stats["dup_signal"] = 0  # 信号去重计数
```

### 终端输出

```
📊 统计信息
   • 接收消息: 150
   • 转发消息: 45
   • 重复消息: 105 (内存: 30 / 哈希: 25 / 语义: 40 / 信号: 10)
                                                          ^^^^^^^^
                                                          新增
   • 错误次数: 2
```

### 日志输出

**检测到重复**:
```
🔄 信号内容与近期重复,跳过转发: source=unusual_whales
```

**DEBUG 模式** (可选):
```python
logger.debug(
    "Signal dedup check: similarity=%.2f, common_chars=%d, threshold=%.2f",
    similarity_ratio,
    common_chars,
    self.similarity_threshold,
)
```

## 测试

### 单元测试

**位置**: `tests/test_signal_deduplicator.py`

**覆盖场景**:

1. **完全相同的摘要** → 检测为重复 ✅
2. **相似但不完全相同的摘要** → 检测为重复 ✅
3. **相同摘要,不同元数据** → 不是重复 ✅
4. **时间窗口过期** → 不是重复 ✅
5. **完全不同的摘要** → 不是重复 ✅
6. **空摘要** → 不是重复 ✅
7. **仅数字/URL 不同** → 检测为重复 ✅
8. **大小写不同的元数据** → 检测为重复 ✅
9. **字符集重叠不足** → 不是重复 ✅
10. **多信号序列** → 正确检测 ✅

### 运行测试

```bash
# 运行所有信号去重测试
python3 -m pytest tests/test_signal_deduplicator.py -v

# 运行单个测试
python3 -m pytest tests/test_signal_deduplicator.py::TestSignalMessageDeduplicator::test_similar_summaries_detected -v
```

**预期输出**:
```
tests/test_signal_deduplicator.py::TestSignalMessageDeduplicator::test_basic_duplicate_detection PASSED
tests/test_signal_deduplicator.py::TestSignalMessageDeduplicator::test_similar_summaries_detected PASSED
...
======================== 10 passed in 0.07s ========================
```

## 性能考虑

### 时间复杂度

- **单次检查**: O(N × M)
  - N = 时间窗口内的信号数量
  - M = 摘要文本长度 (SequenceMatcher)

- **典型场景**:
  - 窗口 6 小时,高频信号 100 条
  - 摘要长度 ~100 字符
  - 检查耗时 < 1ms (可忽略)

### 内存占用

- **单条记录**: ~500 字节
  - normalized_summary: ~200 字节
  - char_set: ~100 字节
  - metadata: ~100 字节
  - timestamp: 8 字节

- **6 小时窗口,100 条信号**: ~50 KB (可忽略)

### 优化建议

**高频场景** (每小时 >50 条信号):
```bash
# 缩短窗口
SIGNAL_DEDUP_WINDOW_MINUTES=180  # 3小时

# 或使用更严格阈值减少存储
SIGNAL_DEDUP_SIMILARITY=0.75
```

**低频场景** (每小时 <10 条信号):
```bash
# 延长窗口防止重复
SIGNAL_DEDUP_WINDOW_MINUTES=720  # 12小时
```

## 边缘情况处理

### 1. 空摘要

```python
if not normalized_summary:
    return False  # 不视为重复,允许通过
```

### 2. 元数据缺失

```python
def _normalize_metadata(...):
    def _norm(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", (value or "").strip())
        return normalized.lower()

    return (
        _norm(action),      # 空字符串 → ""
        _norm(direction),   # None → ""
        ...
    )
```

**结果**: 空值视为有效元数据,可以匹配

### 3. 极短文本

**示例**: "BTC 上涨" vs "ETH 上涨"

```python
# 相似度可能很高 (0.5+),但字符集重叠少
common_chars = len({"btc", "上", "涨"} & {"eth", "上", "涨"})  # 2
if common_chars < min_common_chars:  # 10
    continue  # 不视为重复 ✅
```

### 4. 时间戳更新

**关键设计**:
```python
if entry.metadata == metadata and similarity >= threshold:
    entry.timestamp = now  # 更新时间戳,延长生命周期
    return True
```

**原因**: 如果同一信号持续出现,保持去重有效性

## 与现有去重的协同

### 四层去重顺序

```
1. 内存去重 (MessageDeduplicator)
   ↓ 通过
2. 哈希去重 (compute_sha256)
   ↓ 通过
3. 语义去重 (embedding similarity)
   ↓ 通过
4. AI 分析
   ↓
5. 信号去重 (SignalMessageDeduplicator) ← 新增
   ↓ 通过
6. 转发到 Telegram
```

### 互补性

| 去重层级 | 检测目标 | 典型场景 |
|---------|---------|---------|
| **内存去重** | 完全相同的原始文本 | 短时间内重复发送同一消息 |
| **哈希去重** | 数据库中的完全相同文本 | 历史消息重新发送 |
| **语义去重** | 语义相似的原始事件 | 相同事件的不同表述 |
| **信号去重** ✨ | AI 生成的相似信号 | 不同来源报道同一事件 |

### 实际效果

**场景**: 特朗普贸易政策新闻

```
来源 1: 华盛顿邮报 "Trump cancels Canada trade talks"
来源 2: Twitter "Trump ends negotiations with Canada"

→ 原始文本不同 → 通过内存/哈希去重 ✅
→ 语义相似,但低于阈值 → 通过语义去重 ✅
→ AI 分析生成相似信号 → 被信号去重拦截 ✅
```

**结果**: 用户只看到 1 条信号,而不是 2 条 ✅

## 故障排查

### 问题 1: 误报 (不同信号被判为重复)

**症状**: 有效信号被错误过滤

**诊断**:
```python
# 临时启用 DEBUG 日志
logger.setLevel(logging.DEBUG)
```

**解决**:
```bash
# 提高阈值
SIGNAL_DEDUP_SIMILARITY=0.80
SIGNAL_DEDUP_MIN_COMMON_CHARS=15
```

### 问题 2: 漏检 (重复信号未被检测)

**症状**: 相似信号重复出现

**诊断**:
```python
# 检查归一化结果
normalized1 = SignalMessageDeduplicator._normalize_text(summary1)
normalized2 = SignalMessageDeduplicator._normalize_text(summary2)
print(f"Normalized 1: {normalized1}")
print(f"Normalized 2: {normalized2}")

from difflib import SequenceMatcher
ratio = SequenceMatcher(None, normalized1, normalized2).ratio()
print(f"Similarity: {ratio}")
```

**解决**:
```bash
# 降低阈值
SIGNAL_DEDUP_SIMILARITY=0.60
SIGNAL_DEDUP_MIN_COMMON_CHARS=5
```

### 问题 3: 统计不准确

**症状**: `dup_signal` 计数为 0 但应该有重复

**诊断**:
```bash
# 检查配置
echo $SIGNAL_DEDUP_ENABLED
```

**解决**:
```bash
# 确保启用
SIGNAL_DEDUP_ENABLED=true
```

## 未来改进

### 可能的优化

1. **语义 Embedding 去重**:
   - 使用 `sentence-transformers` 计算摘要 embedding
   - 替代 SequenceMatcher (更准确但更慢)

2. **模糊哈希 (Simhash)**:
   - 比 SequenceMatcher 更快
   - 适合超大规模场景

3. **LRU 缓存**:
   - 使用 `functools.lru_cache` 缓存归一化结果
   - 减少重复计算

4. **持久化**:
   - 将去重记录存入 Redis/数据库
   - 跨进程/重启共享状态

5. **自适应阈值**:
   - 根据历史误报/漏检率自动调整阈值

## 总结

**核心价值**:
- ✅ 解决 AI 信号重复问题
- ✅ 提升用户体验 (减少噪音)
- ✅ 保持高精度 (可配置阈值)
- ✅ 性能开销可忽略 (< 1ms)

**最佳实践**:
- 使用默认配置 (0.68 阈值,6 小时窗口)
- 监控 `dup_signal` 统计
- 根据实际效果微调阈值

**兼容性**:
- ✅ 与现有去重机制完全兼容
- ✅ 可独立启用/禁用
- ✅ 零侵入式集成
