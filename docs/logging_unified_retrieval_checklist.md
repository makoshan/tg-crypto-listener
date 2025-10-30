# 统一检索方案日志检查清单

## 概述

本文档列出所有关键日志点，用于验证统一检索方案是否正确生效。

## 关键日志点

### 1. SupabaseMemoryRepository.fetch_memories()

**位置**: `src/memory/repository.py:67-76`

**预期日志**:
```
🔍 SupabaseMemoryRepository: 开始统一检索 (search_memory RPC，不是 search_memory_events) - 
match_threshold=0.85, match_count=3, min_confidence=0.6, ...
```

**验证点**:
- ✅ 明确说明使用 `search_memory RPC`
- ✅ 明确说明**不是** `search_memory_events`
- ✅ 显示检索参数（threshold, count, confidence 等）

### 2. MemoryRepository.search_memory()

**位置**: `src/db/repositories.py:192-198`

**预期日志**:
```
🔍 MemoryRepository: 调用统一检索 RPC 'search_memory' (不是 search_memory_events) - 
embedding=有/无, keywords=X, assets=Y, threshold=0.85
```

**验证点**:
- ✅ 明确说明调用的是 `search_memory` RPC
- ✅ 明确说明**不是** `search_memory_events`
- ✅ 显示输入参数统计

**后续日志** (`src/db/repositories.py:228-233`):
```
memory.search: supabase hits → total=X, vector=Y, keyword=Z
```

**验证点**:
- ✅ 显示检索结果统计
- ✅ 区分 vector 和 keyword 命中数量

### 3. SupabaseMemoryRepository 检索结果

**位置**: `src/memory/repository.py:96-98`

**预期日志**:
```
✅ 统一检索完成: total=X, vector=Y, keyword=Z
```

**验证点**:
- ✅ 显示总命中数
- ✅ 显示向量命中数
- ✅ 显示关键词命中数

### 4. fetch_memory_evidence() 协调器

**位置**: `src/memory/coordinator.py:39-44`

**预期日志**:
```
🔍 fetch_memory_evidence: 开始统一检索协调 - 
embedding=有/无, keywords=X, assets=Y
```

**验证点**:
- ✅ 明确标识协调器开始执行
- ✅ 显示输入参数

**成功日志** (`src/memory/coordinator.py:66-71`):
```
✅ fetch_memory_evidence: Supabase 统一检索成功 - 
total=X, vector=Y, keyword=Z
```

**降级日志** (`src/memory/coordinator.py:74-77`):
```
⚠️  fetch_memory_evidence: Supabase 返回空结果，降级到本地关键词 - stats={...}
```

或 (`src/memory/coordinator.py:79-81`):
```
⚠️  fetch_memory_evidence: Supabase 统一检索失败，降级到本地关键词 - error=...
```

**验证点**:
- ✅ 成功时显示统计信息
- ✅ 失败/空结果时明确说明降级逻辑

## 错误日志

### RPC 调用失败

**位置**: `src/db/repositories.py:203`

**预期日志**:
```
memory.search: RPC failed, will degrade to local. error=...
```

**位置**: `src/memory/repository.py:111`

**预期日志**:
```
统一检索 RPC search_memory 失败: ...
```

**验证点**:
- ✅ 明确说明是 `search_memory` RPC 失败
- ✅ 说明降级策略

## 不应出现的日志

### ❌ 旧方案日志（不应出现）

以下日志如果出现，说明仍有代码使用旧方案：

```
🔍 Supabase RPC 调用开始: search_memory_events
🔍 调用 search_memory_events RPC: ...
Supabase RPC search_memory_events 失败: ...
```

## 日志追踪流程

### 正常流程（向量检索）

```
1. 🔍 SupabaseMemoryRepository: 开始统一检索 (search_memory RPC，不是 search_memory_events)
2. 🔍 MemoryRepository: 调用统一检索 RPC 'search_memory' (不是 search_memory_events)
3. memory.search: supabase hits → total=X, vector=Y, keyword=0
4. ✅ 统一检索完成: total=X, vector=Y, keyword=0
```

### 关键词降级流程

```
1. 🔍 SupabaseMemoryRepository: 开始统一检索 (search_memory RPC，不是 search_memory_events)
2. 🔍 MemoryRepository: 调用统一检索 RPC 'search_memory' (不是 search_memory_events)
3. memory.search: supabase hits → total=X, vector=0, keyword=Y
4. ✅ 统一检索完成: total=X, vector=0, keyword=Y
```

### 协调器流程

```
1. 🔍 fetch_memory_evidence: 开始统一检索协调
2. 🔍 MemoryRepository: 调用统一检索 RPC 'search_memory' (不是 search_memory_events)
3. memory.search: supabase hits → total=X, vector=Y, keyword=Z
4. ✅ fetch_memory_evidence: Supabase 统一检索成功 - total=X, vector=Y, keyword=Z
```

### 降级流程

```
1. 🔍 fetch_memory_evidence: 开始统一检索协调
2. 🔍 MemoryRepository: 调用统一检索 RPC 'search_memory' (不是 search_memory_events)
3. ⚠️  fetch_memory_evidence: Supabase 返回空结果，降级到本地关键词
   或
   ⚠️  fetch_memory_evidence: Supabase 统一检索失败，降级到本地关键词
```

## 验证方法

### 方法 1: 运行测试脚本

```bash
# 运行日志验证脚本
uvx --with-requirements requirements.txt python -m scripts.verify_unified_retrieval_logs

# 运行功能测试（查看实际日志）
uvx --with-requirements requirements.txt python -m scripts.test_unified_memory_retrieval \
  --keywords bitcoin etf
```

### 方法 2: 检查代码

```bash
# 确认没有旧 RPC 调用
grep -r "search_memory_events" src/ --exclude-dir=__pycache__

# 应该只返回空结果或文档引用，不应该有实际代码调用

# 确认有新 RPC 日志
grep -r "search_memory.*RPC" src/ --exclude-dir=__pycache__
```

### 方法 3: 查看实际运行日志

在生产或测试环境中，搜索日志文件：

```bash
# 查看最近的相关日志
grep -E "统一检索|search_memory RPC|fetch_memory_evidence" logs/app.log | tail -20

# 确认没有旧日志
grep "search_memory_events" logs/app.log
# 应该返回空或只有历史日志
```

## 日志级别建议

- **INFO**: 关键流程日志（检索开始、完成、统计）
- **DEBUG**: 详细调试信息（参数详情、逐条记录处理）
- **WARNING**: 错误和降级情况

## 示例：正确的日志输出

```
2025-01-15 10:00:00 - src.memory.repository - INFO - 🔍 SupabaseMemoryRepository: 开始统一检索 (search_memory RPC，不是 search_memory_events) - match_threshold=0.85, match_count=3, min_confidence=0.6, time_window_hours=72, asset_filter=[], keywords=2, embedding=无
2025-01-15 10:00:00 - src.db.repositories - INFO - 🔍 MemoryRepository: 调用统一检索 RPC 'search_memory' (不是 search_memory_events) - embedding=无, keywords=2, assets=0, threshold=0.85
2025-01-15 10:00:01 - src.db.repositories - INFO - memory.search: supabase hits → total=3, vector=0, keyword=3
2025-01-15 10:00:01 - src.memory.repository - INFO - ✅ 统一检索完成: total=3, vector=0, keyword=3
```

## 故障排查

### 如果看到旧日志

1. 检查是否有其他代码路径仍在调用旧方法
2. 确认所有导入都已更新
3. 检查是否有缓存的代码或进程未重启

### 如果日志不完整

1. 检查日志级别设置（确保 INFO 级别启用）
2. 确认 logger 配置正确
3. 检查是否有日志被过滤或重定向
