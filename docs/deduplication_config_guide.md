# 消息去重配置指南

## 概述

tg-crypto-listener 实现了**四层去重机制**，有效防止重复消息和重复信号：

1. **内存窗口去重** (`MessageDeduplicator`): 基于文本哈希的快速去重
2. **数据库哈希去重**: 检查数据库中完全相同的消息
3. **语义向量去重**: 使用 embedding 检测语义相似的消息
4. **信号级别去重** (`SignalMessageDeduplicator`): 检测 AI 生成的相似交易信号

## 配置参数

### 原始消息去重窗口

**配置项**: `DEDUP_WINDOW_HOURS`

**说明**: 控制 `MessageDeduplicator` 的内存窗口大小，检查最近 N 小时内的消息文本。

**默认值**: `24` (小时)

**示例**: 设置为 4 小时
```bash
DEDUP_WINDOW_HOURS=4
```

**工作原理**:
- 对消息文本进行 MD5 哈希
- 在内存中维护最近 N 小时的消息哈希
- 如果新消息的哈希已存在，则视为重复

### AI 信号去重窗口

**配置项**: `SIGNAL_DEDUP_WINDOW_MINUTES`

**说明**: 控制 `SignalMessageDeduplicator` 的时间窗口，检测 AI 生成的相似信号。

**默认值**: `360` (分钟 = 6 小时)

**示例**: 设置为 4 小时 (240 分钟)
```bash
SIGNAL_DEDUP_WINDOW_MINUTES=240
```

**相关配置**:
- `SIGNAL_DEDUP_ENABLED`: 是否启用信号去重（默认 `true`）
- `SIGNAL_DEDUP_SIMILARITY`: 文本相似度阈值 0.0-1.0（默认 `0.68`）
- `SIGNAL_DEDUP_MIN_COMMON_CHARS`: 最小公共字符数（默认 `10`）

**工作原理**:
1. 归一化 AI 生成的摘要文本（移除 URL、数字、标点）
2. 检查元数据是否匹配（action, direction, event_type, asset）
3. 使用 `SequenceMatcher` 计算文本相似度
4. 如果相似度 >= 阈值且公共字符数 >= 最小值，则视为重复

## 推荐配置：4 小时去重窗口

如果你希望将去重窗口统一设置为 **4 小时**，可以在 `.env` 文件中添加：

```bash
# 原始消息去重：4 小时
DEDUP_WINDOW_HOURS=4

# AI 信号去重：4 小时 (240 分钟)
SIGNAL_DEDUP_ENABLED=true
SIGNAL_DEDUP_WINDOW_MINUTES=240
SIGNAL_DEDUP_SIMILARITY=0.68
SIGNAL_DEDUP_MIN_COMMON_CHARS=10
```

## 不同场景的配置建议

### 高频交易场景

如果你监听的是高频更新的频道，可能需要更长的去重窗口：

```bash
# 原始消息：8 小时
DEDUP_WINDOW_HOURS=8

# AI 信号：12 小时
SIGNAL_DEDUP_WINDOW_MINUTES=720
SIGNAL_DEDUP_SIMILARITY=0.68
```

### 低频精准场景

如果你希望减少误判，可以使用更短的窗口和更严格的相似度要求：

```bash
# 原始消息：2 小时
DEDUP_WINDOW_HOURS=2

# AI 信号：3 小时，更高相似度要求
SIGNAL_DEDUP_WINDOW_MINUTES=180
SIGNAL_DEDUP_SIMILARITY=0.75
SIGNAL_DEDUP_MIN_COMMON_CHARS=15
```

### 宽松去重场景

如果你希望允许更多消息通过（更宽松的去重）：

```bash
# 原始消息：1 小时
DEDUP_WINDOW_HOURS=1

# AI 信号：2 小时，较低相似度要求
SIGNAL_DEDUP_WINDOW_MINUTES=120
SIGNAL_DEDUP_SIMILARITY=0.60
SIGNAL_DEDUP_MIN_COMMON_CHARS=5
```

## 其他去重机制配置

### 语义向量去重

**配置项**: `EMBEDDING_SIMILARITY_THRESHOLD`, `EMBEDDING_TIME_WINDOW_HOURS`

```bash
# 语义相似度阈值 (0.0-1.0)
EMBEDDING_SIMILARITY_THRESHOLD=0.85

# 检查最近 72 小时的语义相似消息
EMBEDDING_TIME_WINDOW_HOURS=72
```

### 优先级 KOL 去重

对于高优先级 KOL，可以设置更严格的去重阈值：

```bash
# 优先级 KOL 强制转发（跳过部分去重检查）
PRIORITY_KOL_FORCE_FORWARD=true

# 优先级 KOL 去重阈值（默认 0.95，更宽松）
PRIORITY_KOL_DEDUP_THRESHOLD=0.95
```

## 验证配置

修改配置后，重启服务并查看日志：

```bash
npm run restart
npm run logs
```

你应该能在日志中看到去重统计：

```
运行统计:
- 总接收: 100
- 重复消息: 15
  - 内存去重: 8
  - 哈希去重: 5
  - 语义去重: 2
  - 信号去重: 0
- 已转发: 85
```

## 注意事项

1. **内存使用**: `DEDUP_WINDOW_HOURS` 越大，内存占用越高。建议根据实际消息量调整。
2. **性能影响**: 语义向量去重需要调用 OpenAI API 生成 embedding，会产生额外成本和延迟。
3. **相似度阈值**: `SIGNAL_DEDUP_SIMILARITY` 过高可能导致误判，过低可能漏检重复。
4. **时间窗口**: 窗口太长可能导致漏掉真实的新消息，窗口太短可能导致重复转发。

## 故障排查

如果发现仍有重复消息：

1. **检查日志**: 查看 `dup_memory`、`dup_hash`、`dup_semantic`、`dup_signal` 统计
2. **调整阈值**: 如果 `dup_signal` 为 0 但仍有重复，尝试降低 `SIGNAL_DEDUP_SIMILARITY`
3. **验证配置**: 确认 `.env` 中的配置已正确加载（查看启动日志）
4. **检查数据库**: 如果启用了 `ENABLE_DB_PERSISTENCE`，确认数据库连接正常

更多详情请参考：
- `docs/signal_deduplication.md`: 信号去重详细说明
- `src/utils.py`: `MessageDeduplicator` 和 `SignalMessageDeduplicator` 实现
