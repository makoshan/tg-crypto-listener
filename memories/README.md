# Memories 目录说明

本目录存储 AI 记忆数据，用于跨会话学习和模式识别。

## 目录结构

```
memories/
  patterns/           # 信号分析模式（由 Claude 或手动维护）
    core.json         # 通用核心模式（初始种子数据）
    listing.json      # 上币消息模式（自动生成）
    hack.json         # 黑客事件模式（自动生成）
    regulation.json   # 监管消息模式（自动生成）
    ...

  assets/             # 按资产分类（Claude Memory Tool 自动创建）
    BTC_2025-10.md
    ETH_recent.md
    ...

  sources/            # 按来源分类（Claude Memory Tool 自动创建）
    MarketNewsFeed_patterns.md
    EWCLNEWS_reliability.md
    ...
```

## 文件格式

### JSON 格式（用于 Gemini 快速读取）

```json
{
  "patterns": [
    {
      "id": "unique-id",
      "timestamp": "2025-10-06T00:00:00+00:00",
      "event_type": "listing",
      "assets": ["BTC", "ETH"],
      "action": "buy",
      "confidence": 0.8,
      "similarity": 1.0,
      "summary": "简要摘要",
      "notes": "详细说明"
    }
  ]
}
```

### Markdown 格式（由 Claude Memory Tool 自动创建）

Claude 会根据任务需求自主决定文件路径、格式和内容结构，无需人工干预。

## 使用说明

### Local 模式（`MEMORY_BACKEND=local`）
- Gemini 读取 `patterns/*.json` 进行关键词匹配
- Claude Memory Tool 创建 `*.md` 文件存储深度分析模式
- 定期归纳任务将 Markdown 转为 JSON 供 Gemini 使用

### Supabase 模式（`MEMORY_BACKEND=supabase`）
- 本目录仅作为备份
- 实际记忆存储在 Supabase `memory_entries` 表
- Claude Memory Tool 写入会被拦截并转为数据库操作

### Hybrid 模式（`MEMORY_BACKEND=hybrid`）
- Supabase 为主存储（向量检索）
- Local 为备份（降级时使用）
- Claude Memory Tool 双写

## 维护

- **备份**：定期运行 `tar -czf memories_backup_$(date +%Y%m%d).tar.gz memories/`
- **清理**：删除 90 天前的 `assets/` 文件（保留 `patterns/` 永久）
- **审查**：定期检查 Claude 创建的文件结构，发现异常时调整 System Prompt

## 安全

- **路径穿越防护**：`MemoryToolHandler._validate_path()` 确保所有文件操作在 `memories/` 目录内
- **内容审查**：`_sanitize_content()` 过滤危险模式（防止 Prompt Injection）
- **审计日志**：所有写操作记录到日志文件

## 参考

- [Memory & Context Management Cookbook](../docs/memory_cookbook.ipynb)
- [本地记忆集成方案](../docs/memory_local_plan.md)
