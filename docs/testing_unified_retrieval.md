# 统一检索方案测试指南

## 概述

本文档说明如何测试和验证 `retrieval_augmentation.md` 中定义的统一检索方案是否正确生效。

## 测试目标

验证以下功能是否正常工作：

1. ✅ `SupabaseMemoryRepository.fetch_memories()` 使用 `search_memory` RPC（不是 `search_memory_events`）
2. ✅ 支持向量优先 + 关键词自动降级
3. ✅ `fetch_memory_evidence()` 协调器正确工作
4. ✅ 日志输出显示统一检索方案

## 快速测试

### 1. 运行完整测试

```bash
# 使用关键词测试（不需要 embedding）
uvx --with-requirements requirements.txt python -m scripts.test_unified_memory_retrieval \
  --keywords bitcoin etf listing

# 使用资产过滤
uvx --with-requirements requirements.txt python -m scripts.test_unified_memory_retrieval \
  --keywords whale \
  --assets BTC ETH

# 仅测试 SupabaseMemoryRepository
uvx --with-requirements requirements.txt python -m scripts.test_unified_memory_retrieval \
  --test-mode repo \
  --keywords bitcoin

# 仅测试协调器
uvx --with-requirements requirements.txt python -m scripts.test_unified_memory_retrieval \
  --test-mode coordinator \
  --keywords etf
```

### 2. 检查日志输出

**✅ 正确的日志（新方案生效）：**

```
🔍 统一检索开始 (search_memory RPC): match_threshold=0.85, match_count=5, ...
✅ 统一检索完成: total=3, vector=2, keyword=1
```

**❌ 错误的日志（旧方案仍在使用）：**

```
🔍 Supabase RPC 调用开始: search_memory_events
```

### 3. 单元测试

```bash
# 运行所有 memory 测试
pytest tests/memory/ -v

# 运行统一检索方案测试
pytest tests/memory/test_unified_retrieval.py -v -s

# 运行底层 RPC 测试
pytest tests/db/test_search_memory_repo.py -v
```

## 测试脚本说明

### `scripts/test_unified_memory_retrieval.py`

完整的功能测试脚本，测试整个统一检索流程。

**功能：**
- 测试 `SupabaseMemoryRepository.fetch_memories()` 是否正确使用新 RPC
- 测试 `fetch_memory_evidence()` 协调器
- 验证关键词降级逻辑
- 输出详细的测试结果和日志

**使用示例：**

```bash
# 基本测试
python -m scripts.test_unified_memory_retrieval --keywords bitcoin

# 完整测试（需要 Supabase 配置）
export SUPABASE_URL=...
export SUPABASE_SERVICE_KEY=...
python -m scripts.test_unified_retrieval --keywords bitcoin etf --assets BTC
```

### `scripts/test_search_memory.py`

底层 RPC 测试脚本，直接测试 `MemoryRepository.search_memory()`。

**功能：**
- 测试 `search_memory` RPC 函数
- 验证返回格式和统计信息
- 可用于调试 Supabase RPC 函数本身

**使用示例：**

```bash
python -m scripts.test_search_memory --keywords bitcoin etf
python -m scripts.test_search_memory --keywords whale --assets BTC ETH
```

## 代码变更验证清单

### ✅ 已完成的更改

1. **`src/memory/repository.py`**
   - ✅ `SupabaseMemoryRepository.fetch_memories()` 现在使用 `UnifiedMemoryRepository.search_memory()`
   - ✅ 支持 `keywords` 参数
   - ✅ 日志显示 "统一检索开始 (search_memory RPC)"
   - ✅ 根据 `news_event_id` 查询完整信号信息

2. **`src/memory/hybrid_repository.py`**
   - ✅ 传递 `keywords` 参数给 `SupabaseMemoryRepository`

3. **`src/memory/coordinator.py`**
   - ✅ 已实现 `fetch_memory_evidence()` 协调器
   - ✅ 使用 `MemoryRepository.search_memory()`
   - ✅ 支持本地关键词兜底

### ❌ 不存在的旧调用

运行以下命令确认没有遗留的旧调用：

```bash
# 检查是否有直接调用 search_memory_events 的地方
grep -r "search_memory_events" src/ --exclude-dir=__pycache__

# 应该只返回文档中的引用，不应该有实际代码调用
```

## 验收标准

### 功能验收

- [ ] 测试脚本可以正常运行
- [ ] 日志显示使用 `search_memory` RPC
- [ ] 向量检索正常工作
- [ ] 关键词降级正常工作
- [ ] 本地关键词兜底正常工作
- [ ] 返回结果格式正确（包含 match_type 统计）

### 性能验收

- [ ] 检索速度正常（< 2秒）
- [ ] 正确使用向量优先策略
- [ ] 降级逻辑不影响性能

### 日志验收

- [ ] 日志清晰显示检索方式（vector/keyword）
- [ ] 统计信息准确（total, vector, keyword 计数）
- [ ] 错误处理日志清晰

## 故障排查

### 问题 1: 仍然看到 `search_memory_events` 日志

**原因：** 可能有其他代码路径仍在使用旧方法。

**解决：**
```bash
# 1. 搜索所有调用点
grep -r "fetch_memories\|search_memory_events" src/

# 2. 检查是否所有调用都通过 UnifiedMemoryRepository
# 3. 确认没有直接调用 _client.rpc("search_memory_events", ...)
```

### 问题 2: 测试返回空结果

**可能原因：**
- 数据库中没有匹配记录
- 阈值设置过高
- 时间窗口太短

**解决：**
```bash
# 降低阈值测试
python -m scripts.test_unified_memory_retrieval \
  --keywords bitcoin \
  --match-threshold 0.7 \
  --time-window-hours 168
```

### 问题 3: RPC 函数不存在

**错误信息：** `function search_memory does not exist`

**解决：**
1. 检查 Supabase 是否已创建 `search_memory` 函数
2. 参考 `docs/retrieval_augmentation.md` 中的 SQL 创建函数
3. 确认函数参数和返回格式正确

## 相关文档

- `docs/retrieval_augmentation.md` - 统一检索方案设计文档
- `docs/memory_system_overview.md` - 记忆系统概览
- `scripts/README_test_unified_retrieval.md` - 测试脚本使用说明
