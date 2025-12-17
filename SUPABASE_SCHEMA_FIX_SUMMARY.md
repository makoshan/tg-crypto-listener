# Supabase Schema 修复总结

## 问题诊断

### 根本原因
`news_events` 表的 `created_at` 和 `updated_at` 字段缺少 DEFAULT 值，导致：

1. ❌ **新插入的记录时间戳为 NULL**
2. ❌ **`search_memory` RPC 无法找到这些记录**（72小时时间窗口过滤）
3. ❌ **记忆系统总是返回空结果**

### 受影响记录
- **总数**: ~1000 条记录
- **时间范围**: 最近几天插入的所有记录
- **示例 ID**: 20082-20087, 17816-19846, 等等

### 诊断证据
```sql
-- 查询结果显示
SELECT id, created_at, updated_at, published_at
FROM news_events
ORDER BY id DESC
LIMIT 5;

-- 返回:
-- ID: 20086, created_at: NULL, updated_at: NULL, published_at: 2025-11-02T16:33:08
-- ID: 20085, created_at: NULL, updated_at: NULL, published_at: 2025-11-02T16:32:20
-- ...
```

## 解决方案

### 立即执行（必需）

**在 Supabase SQL Editor 中运行以下 SQL**：

1. 打开 Supabase Dashboard:
   - URL: https://supabase.com/dashboard/project/woxbgotwkbbtiaerzrqu/sql

2. 复制并执行 `quick_fix_timestamps.sql` 文件内容：

```sql
-- Step 1: Update existing NULL timestamps
UPDATE news_events
SET
    created_at = COALESCE(created_at, published_at, now()),
    updated_at = COALESCE(updated_at, published_at, now())
WHERE created_at IS NULL OR updated_at IS NULL;

-- Step 2: Set DEFAULT for future inserts
ALTER TABLE news_events
    ALTER COLUMN created_at SET DEFAULT now();

ALTER TABLE news_events
    ALTER COLUMN updated_at SET DEFAULT now();

-- Step 3: Create trigger for auto-updating updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_news_events_updated_at ON news_events;
CREATE TRIGGER update_news_events_updated_at
    BEFORE UPDATE ON news_events
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

### 为什么 REST API 修复失败？

尝试通过 REST API 批量更新失败的可能原因：

1. **Row Level Security (RLS)** 策略可能阻止了批量更新
2. **权限限制**: Service Key 可能没有 UPDATE 权限（虽然有 INSERT）
3. **REST API 限制**: Supabase REST API 对 DDL 操作支持有限

**解决办法**: 必须通过 SQL Editor 直接执行 SQL 命令（有完整的 PostgreSQL 权限）

## 验证修复

执行 SQL 后，运行以下验证命令：

```bash
PYTHONPATH=. python3 -c "
import asyncio
from src.config import Config
from src.db.supabase_client import get_supabase_client

async def verify():
    config = Config()
    client = get_supabase_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)

    # 检查剩余 NULL 记录
    null_records = await client._request('GET', 'news_events', params={
        'select': 'id',
        'or': '(created_at.is.null,updated_at.is.null)',
    })

    print(f'剩余 NULL 记录: {len(null_records)} 条')

    # 检查最近记录
    recent = await client._request('GET', 'news_events', params={
        'select': 'id,created_at,updated_at',
        'order': 'id.desc',
        'limit': '5'
    })

    print(f'\\n最近 5 条记录:')
    for r in recent:
        print(f'  ID: {r[\"id\"]}, created_at: {r.get(\"created_at\")}, updated_at: {r.get(\"updated_at\")}')

asyncio.run(verify())
"
```

**期望结果**:
- 剩余 NULL 记录: 0 条
- 最近记录都有有效的 `created_at` 和 `updated_at`

## 测试记忆检索

修复后，测试 `search_memory` RPC：

```bash
PYTHONPATH=. python3 -c "
import asyncio
from src.config import Config
from src.db.supabase_client import get_supabase_client

async def test_memory():
    config = Config()
    client = get_supabase_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)

    # 测试 RPC
    result = await client.rpc('search_memory', {
        'query_embedding': None,
        'query_keywords': ['btc', 'bitcoin'],
        'asset_filter': None,
        'match_threshold': 0.5,
        'match_count': 5,
        'time_window_hours': 72,
        'min_confidence': 0.5
    })

    print(f'检索结果: {len(result)} 条')
    if result:
        for r in result[:3]:
            print(f'  - ID: {r.get(\"news_event_id\")}, similarity: {r.get(\"similarity\")}, match_type: {r.get(\"match_type\")}')

asyncio.run(test_memory())
"
```

**期望结果**: 应该返回匹配的历史记录

## 重启 Listener

修复完成后重启listener：

```bash
npm run restart
```

查看日志确认记忆系统正常工作：

```bash
npm run logs
```

**期望看到**:
```
✅ SupabaseMemoryRepository: 统一检索完成 - total=3, vector=1, keyword=2
```

而不是：
```
⚠️ HybridMemoryRepository: Supabase fetch_memories 返回空 MemoryContext (entries=0)
```

## 相关文件

- `fix_news_events_schema.sql` - 完整修复脚本（包含详细注释）
- `quick_fix_timestamps.sql` - 快速修复 SQL（推荐使用）
- `apply_schema_fix.py` - Python 自动化脚本（REST API 限制，未成功）
- `diagnose_db_schema.py` - 诊断工具
- `test_news_event_insert.py` - 测试工具

## 下一步

1. ✅ 在 Supabase SQL Editor 执行 `quick_fix_timestamps.sql`
2. ✅ 验证修复效果（上面的验证命令）
3. ✅ 测试记忆检索
4. ✅ 重启 listener
5. ✅ 监控日志确认正常

## 预防措施

**未来避免此问题**:
- Supabase 表创建时，确保 `created_at` 和 `updated_at` 有 DEFAULT 值
- 使用 migrations 管理 schema 变更
- 定期检查 NULL 值：`SELECT COUNT(*) FROM news_events WHERE created_at IS NULL`

## 技术细节

### 为什么记忆检索返回空？

`search_memory` RPC 函数的 `base` CTE：

```sql
from ts_doc ne
left join v_ai_signals ais on ais.news_event_id = ne.id
where ne.created_at >= now() - (time_window_hours || ' hours')::interval
```

由于 `created_at` 为 NULL:
- `NULL >= (now() - 72 hours)` 返回 `NULL` (不是 `TRUE` 或 `FALSE`)
- SQL 的 WHERE 子句会过滤掉 `NULL` 结果
- 因此所有 NULL 时间戳的记录都被排除

### 修复后的效果

修复后：
1. 所有现有记录的 `created_at` 使用 `published_at` 或 `now()`
2. 新插入的记录自动获得 `created_at` 和 `updated_at`
3. `search_memory` RPC 能够找到这些记录
4. 记忆系统正常工作，AI 分析获得历史上下文
