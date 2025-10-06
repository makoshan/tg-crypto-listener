# Supabase 空结果调试指南

## 问题描述
日志显示 "Supabase 返回空结果，降级到本地检索"，需要查找原因。

## 新增调试日志

### 1. hybrid_repository.py:74-78 (DEBUG 级别)
检索参数记录：
```
Supabase 检索参数: embedding=有/无 (维度=N), asset_codes=[...]
```

### 2. hybrid_repository.py:91-95 (INFO 级别)
空结果详情：
```
Supabase 返回空结果，降级到本地检索 (embedding维度=N, asset_codes=[...])
```

### 3. repository.py:62-70 (DEBUG 级别)
RPC 调用参数：
```
调用 search_memory_events RPC: match_threshold=X, match_count=N, min_confidence=X,
time_window_hours=N, asset_filter=[...], embedding维度=N
```

RPC 返回结果：
```
RPC 返回结果类型: <class 'list'>, 数量: N
```

### 4. repository.py:84 (DEBUG 级别)
结果处理开始：
```
开始处理 N 行 RPC 结果
```

### 5. repository.py:87/101 (DEBUG 级别)
跳过的行：
```
第 X 行: 跳过（非字典类型）
第 X 行: 跳过（summary 为空）- row={...}
```

### 6. repository.py:119-123 (DEBUG 级别)
成功添加的记忆：
```
第 X 行: 添加记忆 - id=abcd1234..., similarity=0.XXX, confidence=0.XXX,
assets=[...], summary=...
```

### 7. repository.py:126 (DEBUG 级别)
处理总结：
```
总共处理得到 N 条有效记忆
```

### 8. repository.py:137-141 (DEBUG 级别)
最终结果：
```
未检索到相似历史记忆 (阈值=X.XX, 时间窗口=Nh)
```

## 排查步骤

1. **确认是否启用 DEBUG 日志**：
   ```bash
   export LOG_LEVEL=DEBUG
   ```

2. **检查调试日志输出**，按顺序查看：
   - embedding 是否为空或维度不对（应该是 1536 for OpenAI）
   - RPC 参数是否合理（threshold, match_count, time_window 等）
   - RPC 是否返回了数据（返回数量）
   - 如果有数据但被跳过，查看具体原因（summary 为空？）
   - 最终得到多少条有效记忆

3. **常见原因**：
   - embedding 为 None 或空数组
   - match_threshold 设置过高（> 0.9）
   - time_window_hours 过短，没有历史数据
   - min_confidence 过高，过滤掉了所有结果
   - 数据库中 summary 字段为空
   - asset_filter 过滤过严，没有匹配的资产

4. **验证数据库**：
   - 检查 Supabase 中是否有数据
   - 检查 search_memory_events RPC 函数是否正常
   - 手动调用 RPC 测试返回结果

## 配置参数位置

检查 `src/config.py` 或环境变量中的：
- `MEMORY_SIMILARITY_THRESHOLD`
- `MEMORY_MAX_NOTES`
- `MEMORY_MIN_CONFIDENCE`
- `MEMORY_LOOKBACK_HOURS`
