# 记忆系统调试日志增强

## 概述

增加了记忆相关的详细调试日志，方便排查记忆检索和使用过程中的问题。

## 改进的文件

### 1. `src/listener.py`

**位置**: 391-413 行

**增强内容**:
- 在记忆检索成功后，根据日志级别显示不同详细程度的信息
- **DEBUG 级别**: 显示完整的记忆详情（ID、assets、action、confidence、similarity、timestamp、摘要）
- **INFO 级别**: 显示简短统计（assets、action、置信度、相似度）

**示例输出** (DEBUG):
```
🧠 记忆注入 Prompt: 3 条历史参考
📚 记忆详情（完整）:
  [1] ID=abc12345... assets=['BTC'] action=hold confidence=0.85 similarity=0.92 time=2025-10-05 14:32:10
      摘要: 比特币因监管政策调整，建议观望等待明确信号
  [2] ID=def67890... assets=['ETH'] action=buy confidence=0.78 similarity=0.88 time=2025-10-04 09:15:23
      摘要: 以太坊 ETF 获批，市场情绪积极，建议适度买入
```

**示例输出** (INFO):
```
🧠 记忆注入 Prompt: 3 条历史参考
  [1] ['BTC'] hold (conf=0.85, sim=0.92)
  [2] ['ETH'] buy (conf=0.78, sim=0.88)
  [3] ['SOL'] sell (conf=0.72, sim=0.85)
```

---

### 2. `src/memory/repository.py` (Supabase 记忆仓储)

**位置**: 61-85 行

**增强内容**:
- 记录完整的 RPC 调用参数（阈值、时间窗口、资产过滤、embedding 维度）
- 记录 RPC 返回结果的类型和数量
- **空结果告警**: 当 RPC 返回空结果时，自动诊断可能原因（时间窗口太短、阈值太高、无匹配记录等）

**示例输出**:
```
🔍 调用 search_memory_events RPC: match_threshold=0.85, match_count=3, min_confidence=0.6, time_window_hours=168, asset_filter=[], embedding维度=1536
✅ RPC 返回: type=list, count=0
⚠️ RPC 返回空结果 - 可能原因: 1) 时间窗口太短 (168h), 2) 相似度阈值太高 (0.85), 3) 置信度阈值太高 (0.6), 4) 数据库中无匹配记录
```

**位置**: 93-144 行

**增强内容**:
- 显示每行 RPC 结果的原始数据
- 标记跳过的行（非字典类型、summary 为空、assets 字段异常）
- 显示每条成功解析的记忆的详细信息（ID、相似度、置信度、assets、action、摘要）

**示例输出** (DEBUG):
```
📊 开始处理 2 行 RPC 结果
📋 第 0 行原始数据: {'id': 'abc123...', 'created_at': '2025-10-05T14:32:10Z', 'assets': ['BTC'], 'action': 'hold', 'confidence': 0.85, 'similarity': 0.92, 'summary': '比特币因监管政策...'}
✅ 第 0 行: 添加记忆 - id=abc12345..., similarity=0.920, confidence=0.850, assets=['BTC'], action=hold
   摘要: 比特币因监管政策调整，建议观望等待明确信号
📋 第 1 行原始数据: {'id': 'def456...', 'created_at': '2025-10-04T09:15:23Z', 'assets': ['ETH'], ...}
✅ 第 1 行: 添加记忆 - id=def67890..., similarity=0.880, confidence=0.780, assets=['ETH'], action=buy
   摘要: 以太坊 ETF 获批，市场情绪积极，建议适度买入
📈 总共处理得到 2 条有效记忆
```

---

### 3. `src/memory/hybrid_repository.py` (混合记忆仓储)

**位置**: 71-120 行

**增强内容**:
- 记录 Supabase 检索参数（embedding 状态、维度、asset_codes、keywords）
- 区分 Supabase 成功/空结果/失败三种情况
- **DEBUG 级别**: 显示 Supabase 检索到的记忆详情
- **连续失败告警**: 当 Supabase 连续失败达到阈值时，发出 ERROR 级别告警

**示例输出** (Supabase 成功):
```
🌐 Hybrid → Supabase 检索参数: embedding=有 (维度=1536), asset_codes=[], keywords=['listing', 'hack']
✅ Hybrid: 从 Supabase 检索到 2 条记忆
📦 Supabase 记忆详情:
  [1] abc12345... ['BTC'] hold conf=0.85 sim=0.92
  [2] def67890... ['ETH'] buy conf=0.78 sim=0.88
```

**示例输出** (降级到本地):
```
🌐 Hybrid → Supabase 检索参数: embedding=无 (维度=0), asset_codes=[], keywords=['listing']
⚠️  Hybrid: Supabase 返回空结果，降级到本地检索 (embedding维度=0, asset_codes=[], keywords=['listing'])
🔄 Hybrid: 开始本地降级检索 (keywords=['listing'])
✅ Hybrid: 从本地检索到 1 条记忆（灾备模式）
```

**位置**: 122-151 行

**增强内容**:
- 记录本地降级检索的关键词
- **DEBUG 级别**: 显示本地检索到的记忆详情
- **无结果告警**: 当本地检索也无结果时，发出 WARNING

---

### 4. `src/memory/local_memory_store.py` (本地记忆存储)

**位置**: 128-138 行

**改进内容**:
- 只在 DEBUG 级别显示详细的记忆列表（避免 INFO 级别日志过多）
- 增加时间戳显示

---

## 日志级别使用指南

### 生产环境 (LOG_LEVEL=INFO)
- 看到简洁的统计信息：检索到多少条记忆、来源（Supabase/Local）
- 看到关键告警：Supabase 失败、降级、空结果诊断

### 开发/调试环境 (LOG_LEVEL=DEBUG)
- 看到所有详细信息：
  - RPC 调用参数和原始返回数据
  - 每条记忆的完整字段（ID、assets、action、confidence、similarity、timestamp、摘要）
  - 记忆处理过程中的跳过/过滤逻辑

---

## 排查问题示例

### 问题 1: "为什么检索不到记忆？"

**步骤**:
1. 设置 `LOG_LEVEL=DEBUG`
2. 查看日志中的 RPC 参数：
   - `embedding维度=0` → 没有生成 embedding，无法使用 Supabase 向量检索
   - `time_window_hours=24` → 时间窗口太短，尝试增加到 168 (7天)
   - `match_threshold=0.95` → 相似度阈值太高，尝试降低到 0.85
   - `min_confidence=0.8` → 置信度阈值太高，尝试降低到 0.6

### 问题 2: "为什么 Hybrid 总是降级到本地？"

**步骤**:
1. 查看日志中的 Supabase 错误信息：
   - `❌ Hybrid: Supabase 检索失败` → 检查网络连接、Supabase 配置
   - `🚨 Hybrid: Supabase 连续失败 3 次` → 触发降级模式，需要修复 Supabase 连接

### 问题 3: "RPC 返回了数据，但没有被使用？"

**步骤**:
1. 查看 `📊 开始处理 N 行 RPC 结果` 后的日志：
   - `⏭️  第 X 行: 跳过（summary 为空）` → 检查数据库中的 summary 字段
   - `⚠️  第 X 行: assets 字段非列表类型` → 检查数据库 schema
   - `📈 总共处理得到 0 条有效记忆` → 所有数据被过滤，检查数据质量

---

## 性能影响

- **INFO 级别**: 性能影响极小（仅额外日志 I/O）
- **DEBUG 级别**:
  - 会记录大量日志（每条记忆的原始数据、完整字段）
  - 仅建议在开发/调试环境使用
  - 生产环境使用 DEBUG 会显著增加日志存储和 I/O 开销

---

## 相关配置

在 `.env` 中设置日志级别：

```bash
# 生产环境
LOG_LEVEL=INFO

# 开发/调试环境
LOG_LEVEL=DEBUG
```
