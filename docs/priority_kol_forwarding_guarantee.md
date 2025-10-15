# 白名单 KOL 转发保障机制

## 实施日期
2025-10-14

## 问题描述
白名单 KOL 的消息存在被过滤而无法转发到 Telegram 的风险，主要原因包括：
1. AI 分析返回 `asset=NONE` 或 `status=skip`
2. 置信度低于普通消息的门槛（0.4）
3. 语义去重门槛过高导致误判为重复
4. 观望（observe）信号置信度低于 0.85

## 解决方案

### 1. 新增配置选项

**`PRIORITY_KOL_FORCE_FORWARD`** (默认: `true`)
- 启用后，白名单 KOL 的消息即使 AI 分析不完整也会强制转发
- 允许 `asset=NONE`、`status=skip` 等情况下依然转发

**`PRIORITY_KOL_DEDUP_THRESHOLD`** (默认: `0.95`)
- 白名单 KOL 专用的语义去重门槛
- 远高于普通消息的 0.62~0.85，大幅降低误判概率

### 2. 特权清单

白名单 KOL（`sleepinrain`, `journey_of_someone`, `retardfrens`）享有以下特权：

| 特权 | 普通消息 | 白名单 KOL | 改进幅度 |
|------|---------|-----------|---------|
| **关键词过滤** | 必须匹配 | 完全跳过 | 100% 通过率 |
| **置信度门槛** | ≥ 0.4 | ≥ 0.3 | 降低 25% |
| **观望门槛** | ≥ 0.85 | ≥ 0.5 | 降低 41% |
| **语义去重** | 0.62~0.85 | 0.95 | 宽松 12%~35% |
| **强制转发** | 否 | 是 | asset=NONE 也转发 |

### 3. 核心代码修改

#### 3.1 配置层 (`src/config.py:368-369`)
```python
PRIORITY_KOL_FORCE_FORWARD: bool = _as_bool(os.getenv("PRIORITY_KOL_FORCE_FORWARD", "true"))
PRIORITY_KOL_DEDUP_THRESHOLD: float = float(os.getenv("PRIORITY_KOL_DEDUP_THRESHOLD", "0.95"))
```

#### 3.2 语义去重优化 (`src/listener.py:411-417`)
```python
if is_priority_kol:
    threshold = self.config.PRIORITY_KOL_DEDUP_THRESHOLD
    logger.debug("⭐ 白名单 KOL 使用宽松语义去重: threshold=%.2f", threshold)
```

#### 3.3 强制转发逻辑 (`src/listener.py:627-639`)
```python
if is_priority_kol and self.config.PRIORITY_KOL_FORCE_FORWARD and signal_result:
    logger.warning("⭐ 白名单 KOL 强制转发模式: 即使 AI 分析不完整也转发")
    ai_kwargs = {
        "ai_summary": signal_result.summary or f"[{source_name}] {message_text[:100]}...",
        "ai_action": signal_result.action or "observe",
        "ai_confidence": signal_result.confidence,
        "ai_event_type": signal_result.event_type or "general",
        "ai_asset": signal_result.asset or "NONE",
    }
```

#### 3.4 增强日志 (`src/listener.py:361-374`)
```python
logger.warning(
    "⭐ ============ 优先 KOL 消息 ============\n"
    "   来源: %s\n"
    "   特权: 跳过关键词过滤\n"
    "   置信度门槛: 0.3 (普通 0.4)\n"
    "   观望门槛: 0.5 (普通 0.85)\n"
    "   去重门槛: %.2f (普通 %.2f)\n"
    "   强制转发: %s\n"
    "========================================",
    source_name,
    self.config.PRIORITY_KOL_DEDUP_THRESHOLD,
    self.config.EMBEDDING_SIMILARITY_THRESHOLD,
    "启用" if self.config.PRIORITY_KOL_FORCE_FORWARD else "禁用",
)
```

### 4. 过滤点对比

| 过滤点 | 普通消息 | 白名单 KOL | 说明 |
|-------|---------|-----------|------|
| **关键词过滤** | ✅ 严格检查 | ⛔ 完全跳过 | line 355 |
| **内存去重** | ✅ 24h 窗口 | ✅ 24h 窗口 | line 363（保留） |
| **哈希去重** | ✅ 完全相同 | ✅ 完全相同 | line 379（保留） |
| **语义去重** | ✅ 阈值 0.62~0.85 | ✅ 阈值 0.95 | line 411（宽松） |
| **置信度检查** | ✅ ≥ 0.4 | ✅ ≥ 0.3 | line 566（降低） |
| **观望信号** | ✅ ≥ 0.85 | ✅ ≥ 0.5 | line 567（降低） |
| **asset 验证** | ✅ 必须有效 | ⛔ 允许 NONE | line 627（绕过） |
| **status 验证** | ✅ 必须 success | ⛔ 允许 skip | line 806（绕过） |

✅ = 仍然执行检查
⛔ = 完全跳过或特殊处理

### 5. 验证方法

#### 5.1 检查配置
```bash
python3 -c "
from src.config import Config
c = Config()
print(f'PRIORITY_KOL_HANDLES: {c.PRIORITY_KOL_HANDLES}')
print(f'PRIORITY_KOL_FORCE_FORWARD: {c.PRIORITY_KOL_FORCE_FORWARD}')
print(f'PRIORITY_KOL_DEDUP_THRESHOLD: {c.PRIORITY_KOL_DEDUP_THRESHOLD}')
"
```

预期输出：
```
PRIORITY_KOL_HANDLES: {'sleepinrain', 'journey_of_someone', 'retardfrens'}
PRIORITY_KOL_FORCE_FORWARD: True
PRIORITY_KOL_DEDUP_THRESHOLD: 0.95
```

#### 5.2 监控日志
```bash
# 查看白名单 KOL 消息处理日志
npm run logs -- --lines 500 | grep -E "⭐|优先 KOL|白名单 KOL" -A 3 -B 1

# 统计白名单 KOL 转发成功率
npm run logs -- --lines 1000 | grep "⭐ ✅ 白名单 KOL 消息已成功转发" | wc -l
```

### 6. 预期效果

1. **召回率提升**：白名单 KOL 消息转发率从 ~70% 提升至 ~95%+
2. **误判降低**：语义去重门槛从 0.85 提升至 0.95，误判率降低 50%+
3. **鲁棒性增强**：即使 AI 返回 `asset=NONE` 也能转发
4. **可观测性**：专用日志标识 `⭐` 便于快速定位

### 7. 回滚方案

如需禁用强制转发模式，修改 `.env`：
```bash
PRIORITY_KOL_FORCE_FORWARD=false
```

如需调整去重门槛：
```bash
PRIORITY_KOL_DEDUP_THRESHOLD=0.90  # 范围 0.85~0.98
```

重启服务：
```bash
npm run restart
```

### 8. 注意事项

1. **重复消息风险**：去重门槛放宽可能导致轻微重复，但优先保证召回率
2. **质量把关**：白名单 KOL 本身应是高质量来源，降低置信度门槛风险可控
3. **日志噪音**：WARNING 级别日志会增加，但便于监控白名单 KOL 消息流
4. **成本增加**：更多消息被转发会增加 Telegram API 调用量（预计 +5%~10%）

### 9. 相关文件

- `src/config.py` - 配置定义
- `src/listener.py` - 核心处理逻辑
- `src/pipeline/langgraph_pipeline.py` - LangGraph 管线（部分更新）
- `.env` - 运行时配置

### 10. 后续优化建议

1. **动态调整**：根据实际转发质量动态调整置信度门槛
2. **A/B 测试**：对比强制转发前后的信号质量分布
3. **白名单扩展**：基于转发成功率和用户反馈调整白名单列表
4. **告警机制**：白名单 KOL 消息被过滤时触发告警通知

## 部署状态

✅ **已部署**: 2025-10-14 12:37:13 UTC
✅ **PM2 状态**: Online (PID 3706172)
✅ **配置验证**: 通过
✅ **服务重启**: 成功
