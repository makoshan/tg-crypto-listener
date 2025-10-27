# 记忆系统总览

## 概述
- 记忆系统围绕 `news_events` + `ai_signals` 构建，无需额外表即可沉淀历史案例，支撑 Gemini、Claude、Codex 等深度分析引擎。
- 采用 Hybrid 策略：Supabase 向量检索为主，本地 JSON/Markdown 模式为备，必要时扩展多数据源（如文档知识库）。
- 针对高价值信号，引入 Claude Memory Tool 和本地目录，持续提炼模式、案例与学习洞察，实现跨会话增强。

## 目标
- 提供跨会话的上下文记忆，帮助 AI 快速关联类似事件、资产和模式。
- 支持多后端（local / supabase / hybrid），在离线与在线场景间无缝切换。
- 减少重复实现：记忆检索、整合、降级逻辑由 `HybridMemoryRepository` 统一管理。
- 确保可观察性：通过日志、测试与指标掌握记忆读写状况。

## 架构与流程
- **核心数据结构**：
  - `news_events`：存储原始消息与 `text-embedding-3-small` 向量。
  - `ai_signals`：存储分析结果（summary、assets、action、confidence）。
  - RPC `search_memory_events()`：依据向量相似度、置信度与时间窗口筛选历史案例。
- **HybridMemoryRepository**：
  - 首选 Supabase 语义检索；若连接失败或结果为空，降级至本地 JSON 关键词匹配。
  - 配置项：
    ```bash
    MEMORY_SIMILARITY_THRESHOLD=0.40
    MEMORY_MIN_CONFIDENCE=0.6
    MEMORY_LOOKBACK_HOURS=168
    MEMORY_MAX_NOTES=3
    ```
- **多后端模式**：
  - `MEMORY_BACKEND=local`：完全脱机，Claude Memory Tool 写入 Markdown，Gemini 读取本地 JSON。
  - `MEMORY_BACKEND=supabase`：全部读写落在 Supabase，借助向量检索获取语义相似案例。
  - `MEMORY_BACKEND=hybrid`：结合二者优势，常规使用 Supabase，异常时回退本地。
- **多数据源策略**：
  - 在不修改 Supabase Schema 的前提下，通过多连接聚合 `news_events` 与 `docs` 等副库，合并排序后返回 Top-N。
  - `.env` 中新增副库配置，代码端合并并去重，按相似度或权重排序。
- **本地目录结构**（Claude Memory Tool）：
  ```
  memories/claude_cli_deep_analysis/
    ├─ assets/BTC/profile.md
    ├─ patterns/listing_momentum.md
    ├─ case_studies/by_asset/BTC/2025-01-etf.md
    ├─ learning_insights/confidence_calibration.md
    └─ context/session_state.md
  ```
  Claude 通过 `view/create/str_replace/insert/delete/rename` 操作维护该目录，定期清理 30 天前文件。
- **数据流程总览**：
  ```
  Telegram 消息 → news_events (存储 + 向量) → ai_signals (结构化结果)
      ↓                                            ↑
  HybridMemoryRepository.fetch_memories() ← Claude/Gemini 根据配置查询
      ↓
  深度分析引擎 (Gemini/Claude/Codex) ← 记忆注入 → 输出结构化信号
  ```

## 实施步骤
- **Supabase 集成（最小改动版）**
  - 复用现有 `news_events`、`ai_signals`，新增 `search_memory_events()` RPC。
  - `SupabaseMemoryRepository` 根据向量、置信度、时间窗口检索；参数由 `.env` 管理。
- **本地混合方案**
  - Gemini 初筛 90% 场景直接读取本地模式 JSON。
  - 置信度 ≥ 0.75 的高价值信号升级 Claude，使用 Memory Tool 写入 Markdown 档案。
  - 定期任务将 Claude 生成的 Markdown 转换为 JSON 供 Gemini 快速读取。
- **多数据源扩展**
  - 在本地配置副库连接信息（例如 `DOCS_SUPABASE_URL`）。
  - 检索阶段同时查询主库与副库，合并后按相似度排序取 Top3。
  - 不影响现有 Supabase 结构，可按需启用或禁用第二数据源。
- **调试日志增强**
  - `src/listener.py` 中增加 DEBUG 日志：展示记忆条目 ID、assets、action、confidence、similarity、timestamp。
  - INFO 日志提供简化统计，便于生产环境快速确认是否注入记忆。
- **Memory Tool 集成**
  - Anthropic 客户端启用 `memory_tool`，Schema 包含六种命令。
  - Claude 自主决定文件路径与内容格式；系统提示强调先 `view` 相关目录再写入，避免重复创建。
  - 仅在高价值信号触发记忆写入，避免噪声积累。

## 配置
- 通用：
  ```bash
  MEMORY_ENABLED=true
  MEMORY_BACKEND=hybrid        # local / supabase / hybrid
  MEMORY_DIR=./memories        # 本地路径
  MEMORY_MAX_NOTES=3
  MEMORY_SIMILARITY_THRESHOLD=0.40
  MEMORY_MIN_CONFIDENCE=0.6
  MEMORY_LOOKBACK_HOURS=168
  ```
- Supabase：
  ```bash
  SUPABASE_URL=https://xxx.supabase.co
  SUPABASE_SERVICE_KEY=...
  SUPABASE_ANON_KEY=...
  ```
- 多数据源（可选）：
  ```bash
  SECONDARY_SUPABASE_URL=https://...
  SECONDARY_SUPABASE_KEY=...
  ```
- Claude Memory Tool：
  - 启用 `betas=["context-management-2025-06-27"]`
  - 确保 `memories/claude_cli_deep_analysis/` 可读写。

## 最佳实践
- Markdown 优先：记忆文件使用 Markdown 便于 Claude 读写与人类审计。
- 高价值触发：仅在置信度 ≥ 0.75 时写入记忆，避免噪声；普通场景使用 Gemini 的轻量模式。
- 证据引用：在深度分析输出中注明记忆来源与相似度，方便回溯。
- 安全防护：在 Memory Tool Handler 中校验路径，防御目录穿越；对新写入内容执行关键词检查。
- 周期归纳：每日离线任务对新案例做归纳，拆分出模式与失败复盘，更新本地 JSON 种子。

## 验证与测试
- Supabase 连通性：`pytest tests/db/test_supabase.py -v`
- 混合仓库检索：`pytest tests/memory/test_multi_source_repository.py -v`
- 手动验证阈值：调整 `.env` 后通过 CLI 调用 `HybridMemoryRepository.fetch_memories()` 检查返回数量与相似度。
- 观测日志：开启 DEBUG，确认记忆注入条目、降级提示与过期清理信息。

## 里程碑与状态
- Supabase 向量检索已上线，`news_events` 含 embedding 1000+ 条，`ai_signals` 965+ 条，可正常检索。
- 本地混合模式完成架构设计与目录规范，适配 Claude Memory Tool。
- 多数据源方案与调试日志增强已形成实施指南，等待落地/验证。

## 风险与后续
- **性能**：Supabase 向量检索需关注延迟，可考虑创建 HNSW/IVFFlat 索引与缓存策略。
- **一致性**：多后端并存时需定期校验本地/云端记忆是否同步，防止重复或过期数据。
- **安全**：记忆文件可能成为 Prompt 注入向量，需进行内容审计、路径校验与访问控制。
- **清理**：建议定期删除 30 天以上历史或噪声内容，保持记忆质量。
- **后续计划**：
  - 引入胜率统计、资产分组检索、记忆版本化。
  - 构建 A/B 测试评估不同阈值与后端组合的效果。
  - 扩展日志到指标系统，监控记忆命中率、降级次数、Claude 写入频率。
