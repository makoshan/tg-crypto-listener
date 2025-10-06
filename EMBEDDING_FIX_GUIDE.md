# Embedding 格式修复指南

## 🎯 问题描述

**症状**：Memory 功能无法检索到历史记忆，Supabase 向量查询返回 0 条结果

**原因**：`news_events.embedding` 字段被存储为 **字符串** 而非 **PostgreSQL vector 类型**

**影响**：向量相似度查询 (`<=>` 运算符) 无法工作

---

## ✅ 已完成的修复

### 1. 代码修复（已完成）

文件：`src/db/repositories.py:83-87`

```python
# 将 Python list 转换为 PostgreSQL vector 格式字符串
embedding_str = "[" + ",".join(str(v) for v in payload.embedding) + "]"
```

✅ **新写入的数据将使用正确格式**

---

## 🔧 需要执行的数据库迁移

### 方法 1：使用完整脚本（推荐）

1. 登录 **Supabase Dashboard**
2. 进入 **SQL Editor**
3. 打开文件：`docs/fix_embedding_format.sql`
4. 复制全部内容并执行
5. 查看执行日志，确认成功

**特点**：
- ✅ 包含完整的检查、转换、验证流程
- ✅ 自动创建向量索引
- ✅ 详细的日志输出

---

### 方法 2：使用简化脚本

如果完整脚本有问题，使用简化版：

1. 打开文件：`docs/fix_embedding_simple.sql`
2. **逐条复制**每个 SQL 语句到 Supabase SQL Editor
3. 按顺序执行

**关键步骤**：

```sql
-- 核心修复：转换格式
UPDATE news_events
SET embedding = embedding::text::vector(1536)
WHERE embedding IS NOT NULL;

-- 创建索引
CREATE INDEX idx_news_events_embedding
ON news_events
USING ivfflat(embedding vector_cosine_ops)
WITH (lists = 100);
```

---

## 🧪 验证修复

执行迁移脚本后，运行验证工具：

```bash
python3 verify_embedding_issue.py
```

**期望输出**：

```
✅ 找到 5 条记录

记录 #1 (ID: 1863)
  ✅ 格式正确：vector 类型（返回为列表）
  维度: 1536
  前 5 个值: [-0.002, -0.017, ...]

✅ RPC 调用成功
返回结果数量: 3

✅ 成功检索到记忆！
  [1] similarity=0.856, confidence=0.75, assets=['BTC']
  [2] similarity=0.823, confidence=0.80, assets=['ETH']
  [3] similarity=0.791, confidence=0.70, assets=['SOL']
```

---

## 🚀 验证通过后

1. **重启应用**：

```bash
python3 main.py
```

2. **观察日志**，应该看到：

```
🔍 Supabase RPC 调用开始: search_memory_events
✅ Supabase RPC 返回: type=list, count=3
📊 Supabase 返回 3 条记忆:
   - 时间窗口: 168h
   - 相似度阈值: 0.4
   - 置信度阈值: 0.6
```

3. **触发 AI 分析**，验证 Memory 注入：

```
🧠 记忆检索开始: backend=HybridMemoryRepository keywords=['btc']
✅ Hybrid: 从 Supabase 检索到 3 条记忆
🧠 记忆注入 Prompt: 3 条历史参考
```

---

## 📊 预期效果

修复后的改进：

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| Supabase 查询结果 | 0 条 | 3-5 条 |
| Memory 注入 | ❌ 无 | ✅ 有 |
| Claude 深度分析 | 缺少上下文 | ✅ 有历史参考 |
| 语义去重准确度 | 仅本地 | ✅ 全局（Supabase + Local） |

---

## ⚠️ 常见问题

### Q1: 执行 SQL 报错 "cannot cast type text to vector"

**原因**：列类型可能不是 `vector(1536)`

**解决**：

```sql
-- 先修改列类型
ALTER TABLE news_events
ALTER COLUMN embedding TYPE vector(1536)
USING embedding::text::vector(1536);
```

### Q2: 索引创建失败 "extension "vector" is not installed"

**原因**：pgvector 扩展未启用

**解决**：
1. 进入 Supabase Dashboard → **Database** → **Extensions**
2. 搜索 `vector`
3. 点击启用

### Q3: 查询仍然返回 0 结果

**可能原因**：
1. 相似度阈值太高（当前 0.4）
2. 时间窗口太短（当前 168h = 7天）
3. 置信度阈值太高（当前 0.6）

**调整配置**（`.env`）：

```env
MEMORY_SIMILARITY_THRESHOLD=0.3  # 降低相似度阈值
MEMORY_LOOKBACK_HOURS=336  # 增加到 14 天
MEMORY_MIN_CONFIDENCE=0.5  # 降低置信度阈值
```

---

## 📝 技术细节

### 为什么之前是字符串格式？

**原因**：通过 Supabase REST API 传递 Python `list[float]` 时，被自动序列化为 JSON 字符串

```python
# 错误方式（之前）
"embedding": [0.1, 0.2, ...]  # → 存储为 "[-0.1,0.2,...]" 字符串

# 正确方式（现在）
"embedding": "[0.1,0.2,...]"  # → PostgreSQL 识别为 vector 类型
```

### Vector 索引类型选择

- **IVFFlat**（当前使用）：适合中等规模（<1M 记录），查询速度快
- **HNSW**：适合大规模数据，内存占用高但精度更好

如需切换到 HNSW：

```sql
CREATE INDEX idx_news_events_embedding
ON news_events
USING hnsw(embedding vector_cosine_ops);
```

---

## ✅ 检查清单

- [ ] 执行数据库迁移脚本
- [ ] 运行 `python3 verify_embedding_issue.py` 验证
- [ ] 确认输出显示 "✅ 格式正确：vector 类型"
- [ ] 确认 RPC 查询返回 > 0 条结果
- [ ] 重启应用
- [ ] 观察日志确认 Memory 功能正常
- [ ] 触发一条 AI 分析，确认记忆注入

---

## 🎉 完成！

修复完成后，Memory 功能将恢复正常，Claude 深度分析将获得完整的历史上下文！
