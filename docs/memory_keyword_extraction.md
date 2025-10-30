# 记忆检索关键词提取机制

## 概述

记忆检索系统使用关键词来帮助查找相关的历史记忆。本文档说明关键词的来源、提取方式和在记忆检索中的使用。

## 关键词来源

关键词有两个来源，按优先级合并：

### 1. 关键词文件 (`keywords.txt`)

- **默认路径**: `./keywords.txt`（项目根目录）
- **自定义路径**: 通过环境变量 `FILTER_KEYWORDS_FILE` 指定
- **格式**: 
  - 每行一个或多个关键词（逗号分隔）
  - 支持注释（以 `#` 开头）
  - 空行会被忽略

**示例** (`keywords.txt`):
```
# 上币相关
listing,上币,launch

# 黑客攻击
hack,黑客,exploit

# 监管消息
regulation,监管,compliance
```

### 2. 环境变量 (`FILTER_KEYWORDS`)

- **格式**: 逗号分隔的关键词列表
- **示例**: `FILTER_KEYWORDS="btc,eth,listing,hack"`

## 关键词提取流程

当收到一条 Telegram 消息时，系统会执行以下步骤：

### 步骤 1: 消息过滤

```python
# src/listener.py:478
keywords_hit = self._collect_keywords(message_text)
```

系统会扫描原始消息文本，检查是否包含配置的关键词。

### 步骤 2: 翻译后再次提取

```python
# src/listener.py:493-494
if translated_text and translated_text != message_text:
    keywords_hit = self._collect_keywords(message_text, translated_text)
```

如果消息被翻译，系统会同时扫描原文和译文，找出所有匹配的关键词。

### 步骤 3: 关键词匹配逻辑

`_collect_keywords` 方法的实现 (`src/listener.py:1368-1381`):

```python
def _collect_keywords(self, *texts: str) -> list[str]:
    hits: list[str] = []
    available = [text for text in texts if text]
    if not available:
        return hits
    for keyword in self.config.FILTER_KEYWORDS:
        if not keyword:
            continue
        lower_kw = keyword.lower()
        for text in available:
            if lower_kw in text.lower():
                hits.append(keyword)
                break
    return hits
```

**匹配规则**:
- 不区分大小写（转换为小写后比较）
- 子串匹配（如果关键词出现在文本中任意位置即匹配）
- 返回所有匹配的关键词列表（去重）

**示例**:
- 消息: "Lista DAO 推出新的借贷功能..."
- 配置关键词: `["listing", "借贷", "dao"]`
- 匹配结果: `["借贷", "dao"]` （因为 "借贷" 和 "dao" 都出现在消息中）

## 记忆检索使用关键词

提取到的关键词 `keywords_hit` 会被用于记忆检索，不同后端的使用方式不同：

### 1. LocalMemoryStore（本地存储）

```python
# src/listener.py:513-514
memory_entries = self.memory_repository.load_entries(
    keywords=keywords_hit,
    limit=self.config.MEMORY_MAX_NOTES,
    min_confidence=self.config.MEMORY_MIN_CONFIDENCE,
)
```

**使用方式**:
- 根据关键词匹配模式文件名：`memories/patterns/{keyword}.json`
- 例如，如果 `keywords_hit = ["listing", "借贷"]`，会加载：
  - `memories/patterns/listing.json`
  - `memories/patterns/借贷.json`
  - `memories/patterns/core.json`（通用模式）

### 2. SupabaseMemoryRepository（向量检索）

```python
# src/listener.py:537-540
memory_context = await self.memory_repository.fetch_memories(
    embedding=embedding_vector,
    asset_codes=None,
)
```

**使用方式**:
- 优先使用 `embedding` 向量进行语义相似度检索
- 如果没有 `embedding`，可以使用 `keywords` 作为降级策略（通过 RPC `search_memory`）

### 3. HybridMemoryRepository（混合后端）

```python
# src/listener.py:525-529
memory_context = await self.memory_repository.fetch_memories(
    embedding=embedding_vector,
    asset_codes=None,
    keywords=keywords_hit,
)
```

**使用方式**:
- 先尝试 Supabase 向量检索（如果有 embedding）
- 如果失败或结果不足，降级到 LocalMemoryStore 使用关键词检索

## 日志示例解析

您看到的日志：

```
🧠 记忆检索开始: backend=SupabaseMemoryRepository keywords=['Lista', 'BNB', 'slisBNB']
🧠 Memory 检索完成: 5 条记录
  [1] BNB,SLISBNB conf=0.70 sim=0.79 summary=...
```

**说明**:
1. **`keywords=['Lista', 'BNB', 'slisBNB']`**: 这是从消息文本中提取到的匹配关键词
2. **检索方式**: SupabaseMemoryRepository 使用 embedding 向量检索（因为 `keywords` 参数存在但主要用于降级）
3. **结果**: 找到了 5 条相似的历史记忆
4. **显示格式**: `[索引] 资产列表 conf=置信度 sim=相似度 summary=摘要`

## 配置影响

### 关键词文件路径

```bash
# .env
FILTER_KEYWORDS_FILE=./custom_keywords.txt
```

### 记忆检索参数

```bash
# .env
MEMORY_MAX_NOTES=5              # 最多返回几条记忆
MEMORY_MIN_CONFIDENCE=0.6       # 最小置信度阈值
MEMORY_SIMILARITY_THRESHOLD=0.55 # 相似度阈值（向量检索）
MEMORY_LOOKBACK_HOURS=72        # 时间窗口（小时）
```

## 常见问题

### Q: 为什么有些消息没有提取到关键词？

A: 可能原因：
1. 消息文本中不包含任何配置的关键词
2. 关键词配置为空或未正确加载
3. 关键词大小写或拼写不匹配（虽然系统不区分大小写，但需要是子串）

### Q: 关键词提取和消息过滤的关系？

A: 
- 关键词首先用于**消息过滤**（判断消息是否应该被处理）
- 然后用于**记忆检索**（查找相关历史记忆）
- 两者使用相同的关键词配置

### Q: 如何优化关键词配置？

A: 
1. 添加常见的代币符号（如 `BTC`, `ETH`, `BNB`）
2. 添加事件类型关键词（如 `listing`, `hack`, `regulation`）
3. 添加协议名称（如 `Lista`, `Uniswap`, `Aave`）
4. 定期更新关键词以覆盖新的热点

## 相关代码位置

- 关键词加载: `src/config.py:131-159`
- 关键词提取: `src/listener.py:1368-1381`
- 记忆检索入口: `src/listener.py:501-550`
- LocalMemoryStore: `src/memory/local_memory_store.py:55-139`
- SupabaseMemoryRepository: `src/memory/repository.py:37-88`
