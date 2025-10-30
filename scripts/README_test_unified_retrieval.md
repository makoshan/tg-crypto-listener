# 统一检索方案测试说明

## 背景

根据 `docs/retrieval_augmentation.md`，系统应该使用统一的 `search_memory` RPC（向量优先，自动降级关键词），而不是旧的 `search_memory_events` RPC。

## 测试脚本

### 1. 完整测试脚本

```bash
# 测试统一检索方案（包括 SupabaseMemoryRepository 和 fetch_memory_evidence）
uvx --with-requirements requirements.txt python scripts/test_unified_memory_retrieval.py

# 仅测试关键词检索（无 embedding）
uvx --with-requirements requirements.txt python scripts/test_unified_memory_retrieval.py --keywords bitcoin etf

# 测试向量检索（需要提供 embedding 文件）
uvx --with-requirements requirements.txt python scripts/test_unified_memory_retrieval.py \
  --embedding-file path/to/embedding.json \
  --keywords bitcoin

# 测试特定资产过滤
uvx --with-requirements requirements.txt python scripts/test_unified_memory_retrieval.py \
  --keywords listing \
  --assets BTC ETH

# 仅测试 SupabaseMemoryRepository
uvx --with-requirements requirements.txt python scripts/test_unified_memory_retrieval.py --test-mode repo

# 仅测试 fetch_memory_evidence 协调器
uvx --with-requirements requirements.txt python scripts/test_unified_memory_retrieval.py --test-mode coordinator
```

### 2. 底层 RPC 测试（已存在）

```bash
# 测试底层的 MemoryRepository.search_memory() RPC
uvx --with-requirements requirements.txt python scripts/test_search_memory.py --keywords bitcoin etf
```

## 验证点

运行测试后，检查日志输出：

### ✅ 正确的日志（新方案生效）

```
🔍 统一检索开始 (search_memory RPC): ...
✅ 统一检索完成: total=X, vector=Y, keyword=Z
```

### ❌ 错误的日志（旧方案仍在使用）

```
🔍 Supabase RPC 调用开始: search_memory_events
```

## 单元测试

```bash
# 运行所有 memory 相关测试
pytest tests/memory/ -v

# 运行统一检索方案测试
pytest tests/memory/test_unified_retrieval.py -v

# 运行底层 RPC 测试
pytest tests/db/test_search_memory_repo.py -v
```

## 预期行为

1. **向量优先**：如果有 embedding，优先使用向量相似度检索
2. **关键词降级**：向量检索结果不足时，自动使用关键词检索
3. **本地兜底**：如果 Supabase 返回空或异常，`fetch_memory_evidence()` 会降级到本地关键词列表
4. **日志清晰**：日志应显示 `search_memory` RPC 调用，包含命中类型（vector/keyword）统计

## 故障排查

### 问题：仍然看到 `search_memory_events` 日志

**原因**：代码未正确更新，或者有其他地方仍在调用旧方法。

**解决**：
1. 检查 `src/memory/repository.py` 是否使用 `UnifiedMemoryRepository`
2. 确认没有其他地方直接调用 `search_memory_events` RPC
3. 运行 `grep -r "search_memory_events" src/` 查找遗留调用

### 问题：测试返回空结果

**可能原因**：
1. 数据库中确实没有匹配的记录
2. 相似度阈值太高（默认 0.85）
3. 时间窗口太短（默认 72 小时）
4. 置信度阈值太高（默认 0.6）

**解决**：
- 降低阈值：`--match-threshold 0.7`
- 增加时间窗口：`--time-window-hours 168`（7天）
- 降低置信度：`--min-confidence 0.5`

### 问题：返回格式不匹配

**原因**：Supabase 数据库中的 `search_memory` RPC 函数未正确创建或返回格式不同。

**解决**：
1. 检查 Supabase 是否已创建 `search_memory` 函数（参考 `docs/retrieval_augmentation.md`）
2. 验证函数返回格式与代码期望一致
