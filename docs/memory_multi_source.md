# 多数据源记忆集成方案（无需改 Supabase 实例）

## 目标

- 在不修改现有 Supabase 数据库的情况下，把主库 `news_events` 和副库（例如 `docs`）同时纳入 LangGraph 记忆检索。
- 只改本地配置与内存仓代码，可按需开关第二数据源。

## 1. 环境变量

在 `.env` 增加副库配置（如不需要可留空）：

```bash
# 主库（已存在）
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...

# 副库（可选）
SUPABASE_SECONDARY_URL=
SUPABASE_SECONDARY_SERVICE_KEY=
# 可选：控制是否启用副库
SUPABASE_SECONDARY_ENABLED=false
```

> `SUPABASE_SECONDARY_ENABLED=true` 时才会初始化副库 client，避免无配置导致报错。

## 2. 代码改动思路

### 2.1 SupabaseMemoryRepository

1. **初始化**：在构造函数读取副库配置，若启用则创建第二个 Supabase client。
2. **数据源标志**：新增布尔字段 `secondary_enabled`，仅当可用时进行副库查询。

### 2.2 `_fetch_memories` 流程

```
主库检索 news_events → 转换 MemoryEntry
副库检索 docs（可选）→ 转换 MemoryEntry
合并列表 → 去重（按 hash / summary）→ 按 similarity 排序 → 截取 limit
```

### 2.3 副库数据转换建议

- `docs` 表：
  - `content_text` → MemoryEntry.content
  - `ai_summary_cn` → MemoryEntry.summary
  - `tags` → 写入 `metadata["tags"]`
  - `created_at` → MemoryEntry.timestamp

## 3. 伪代码示例

```python
class SupabaseMemoryRepository:
    def __init__(self, primary_client, config):
        self.primary = primary_client
        if config.SECONDARY_ENABLED and config.SECONDARY_URL:
            self.secondary = create_client(
                config.SECONDARY_URL,
                config.SECONDARY_SERVICE_KEY,
            )
        else:
            self.secondary = None

    async def fetch_memories(...):
        memories = []
        memories += await self._fetch_primary(...)
        if self.secondary:
            memories += await self._fetch_secondary(...)
        return self._merge_rank(memories)
```

## 4. 排序与去重

- 现有的 `_merge_rank` / `_deduplicate` 方法可复用。
- 建议按 `similarity`、`confidence`、`timestamp` 复合排序。

## 5. 开关控制

- `.env` 中开启副库再创建 client，默认关闭。
- 可在代码中增加日志提示，用于确认副库启用状态。

## 6. 验证步骤

1. `.env` 填入副库 URL/KEY，并设 `SUPABASE_SECONDARY_ENABLED=true`。
2. 启动应用，观察日志（应打印 “Secondary memory client initialised…”）。
3. 触发记忆检索（Context Gather 节点），确认输出包括 docs 内容。

## 7. 后续扩展

- 更多副库（多于一个）可以用列表配置，遍历取数据。
- 文档型数据可加额外字段，如 `canonical_url`、`source_author`。
- 如需要在 prompt 中区分来源，可在 MemoryEntry.metadata 加上 `{"source": "docs"}`。
