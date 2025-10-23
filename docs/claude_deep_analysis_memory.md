# Claude CLI 深度分析记忆系统设计文档

## 1. 概述

本文档描述了专门为 **Claude CLI 深度分析引擎** 设计的独立记忆管理系统，基于 Anthropic Memory Tool API 实现持久化、结构化的知识积累。

### 1.1 设计目标

- **独立性**：与通用记忆系统（服务于所有 AI 交互）完全隔离
- **专用性**：仅服务于高价值信号（confidence ≥ 0.75）的深度分析
- **智能性**：Claude 自动决策何时读写记忆，无需人工干预
- **持久性**：跨会话保留市场规律、资产档案、历史事件
- **结构化**：分类组织记忆文件，便于检索和维护

### 1.2 与通用记忆系统的对比

| 维度 | 通用记忆系统 | Claude CLI 深度分析记忆 |
|------|-------------|------------------------|
| **服务对象** | 所有 AI 交互（主分析、翻译、对话） | 仅 Claude 深度分析引擎 |
| **触发条件** | 所有消息 | confidence ≥ 0.75 的高价值信号 |
| **存储位置** | `./memories/` | `./memories/claude_cli_deep_analysis/` |
| **后端支持** | Local / Supabase / Hybrid | Memory Tool (文件系统) |
| **生命周期** | 短期（小时/天） | 长期（周/月） |
| **内容类型** | 临时上下文、关键词匹配 | 市场规律、资产档案、历史事件 |

---

## 2. 架构设计

### 2.1 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                  深度分析处理流程                              │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
      ┌──────────────────────────┐
      │   主 AI 分析（Gemini）    │
      │   confidence ≥ 0.75?     │
      └──────────┬───────────────┘
                 │ YES
                 ▼
      ┌──────────────────────────┐
      │ Claude Deep Analysis     │
      │ + Memory Tool            │
      └──────────┬───────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────┐
│          ClaudeDeepAnalysisMemoryHandler                   │
├────────────────────────────────────────────────────────────┤
│  Memory Tool API (6 Commands)                              │
│  ├─ view   : 查看目录/文件内容                             │
│  ├─ create : 创建/覆盖文件                                 │
│  ├─ str_replace : 替换文本                                 │
│  ├─ insert : 插入文本                                       │
│  ├─ delete : 删除文件/目录                                 │
│  └─ rename : 重命名/移动文件                               │
│                                                              │
│  Storage Structure                                          │
│  └─ ./memories/claude_cli_deep_analysis/                   │
│      ├─ assets/              # 资产档案（BTC.md, ETH.md）  │
│      ├─ patterns/            # 市场规律（上线效应.md）      │
│      ├─ events/              # 历史事件（按时间/类型）      │
│      ├─ analysis_rules/      # 分析规则（置信度校准）      │
│      └─ context/             # 上下文状态（会话进度）       │
│                                                              │
│  Auto-Management                                            │
│  ├─ 启动检查: 自动 view /memories/claude_cli_deep_analysis │
│  ├─ 智能读取: 根据 asset/event_type 读取相关文件          │
│  ├─ 增量更新: 完成分析后更新规律和洞察                    │
│  └─ 定期清理: 删除 30 天前的过期文件                      │
└────────────────────────────────────────────────────────────┘
```

### 2.2 目录结构设计

```
./memories/claude_cli_deep_analysis/
├── assets/                          # 资产档案（按资产代码组织）
│   ├── BTC/
│   │   ├── profile.md               # BTC 基本档案
│   │   ├── hack_analysis.md         # 安全事件分析记录
│   │   ├── listing_analysis.md      # 上线事件分析记录
│   │   └── partnership_analysis.md  # 合作事件分析记录
│   ├── ETH/
│   ├── SOL/
│   └── _template/                   # 资产档案模板目录
│       ├── profile_template.md
│       └── analysis_template.md
│
├── patterns/                        # 市场规律（按事件类型组织）
│   ├── hack_analysis.md             # 黑客攻击分析模式
│   ├── regulation_analysis.md       # 监管事件分析模式
│   ├── partnership_analysis.md      # 合作事件分析模式
│   ├── listing_analysis.md          # 上线事件分析模式
│   ├── market_analysis.md           # 市场走势分析模式
│   ├── technical_analysis.md        # 技术更新分析模式
│   └── false_breakout.md            # 假突破识别规则
│
├── case_studies/                    # 案例研究（深度分析历史案例）
│   ├── by_asset/                    # 按资产分类
│   │   ├── BTC/
│   │   │   ├── 2025-01-btc-etf.md
│   │   │   └── 2024-12-spot-approval.md
│   │   ├── ETH/
│   │   └── SOL/
│   ├── by_event_type/               # 按事件类型分类
│   │   ├── security_breach/
│   │   │   ├── 2024-binance-hack.md
│   │   │   └── 2023-ftx-collapse.md
│   │   ├── regulatory_action/
│   │   └── partnership/
│   └── _index.md                    # 案例索引
│
├── market_patterns/                 # 市场模式记忆
│   ├── price_reactions.md           # 价格反应模式
│   ├── sentiment_analysis.md        # 市场情绪分析
│   ├── correlation_patterns.md      # 资产相关性模式
│   └── seasonal_trends.md           # 季节性趋势
│
├── learning_insights/               # 学习洞察记忆
│   ├── successful_predictions.md    # 成功预测案例
│   ├── failed_predictions.md        # 失败预测案例（反思）
│   ├── analysis_improvements.md     # 分析方法改进
│   └── confidence_calibration.md    # 置信度校准历史
│
├── context/                         # 上下文状态（短期）
│   ├── session_state.md             # 当前会话状态
│   └── analysis_progress.md         # 分析进度追踪
│
└── README.md                        # 记忆系统使用指南
```

---

## 3. 核心功能

### 3.1 Memory Tool API 集成

基于 Anthropic Memory Tool 标准实现，支持 6 个核心命令：

#### 3.1.1 启用 Memory Tool

**API 配置**：
```python
# 在 AnthropicClient 初始化时启用
client = AnthropicClient(
    api_key="...",
    model_name="claude-sonnet-4-5-20250929",
    memory_handler=ClaudeDeepAnalysisMemoryHandler(),  # 独立的深度分析记忆处理器
    max_tool_turns=3,
)

# 请求时添加 beta header
betas=["context-management-2025-06-27"]
```

#### 3.1.2 Tool Schema 定义

```json
{
  "name": "memory_tool",
  "description": "Read and write persistent knowledge for deep analysis. Supported commands: view, create, str_replace, insert, delete, rename.",
  "input_schema": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "enum": ["view", "create", "str_replace", "insert", "delete", "rename"]
      },
      "path": {"type": "string"},
      "file_text": {"type": "string"},
      "old_str": {"type": "string"},
      "new_str": {"type": "string"},
      "insert_line": {"type": "integer"},
      "insert_text": {"type": "string"},
      "old_path": {"type": "string"},
      "new_path": {"type": "string"}
    },
    "required": ["command"]
  }
}
```

### 3.2 Claude 自动决策机制

Claude 会根据以下协议自动管理记忆：

#### 3.2.1 启动检查（Claude 自动协议）

Claude 会在每次深度分析开始时自动执行以下协议：

```
IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE.

Use the `view` command to check /memories/claude_cli_deep_analysis/ for:
1. Relevant asset profiles (assets/{ASSET}/profile.md)
2. Historical case studies (case_studies/by_asset/{ASSET}/ and case_studies/by_event_type/{TYPE}/)
3. Market patterns related to this event (patterns/{EVENT_TYPE}_analysis.md)
4. Learning insights and past predictions (learning_insights/)

**Selective reading**: Only read files DIRECTLY relevant to this specific event.
Do NOT read all files - be selective based on asset and event_type.

**Assume interruption**: Your context may be reset at any moment.
Record progress continuously by updating memory files as you work.
```

#### 3.2.2 智能读取策略

Claude 根据事件类型和资产自动选择读取策略：

```python
# 场景 1: BTC 上线消息 (listing event)
→ Claude 自动读取:
  1. assets/BTC/profile.md                      # BTC 基本档案
  2. assets/BTC/listing_analysis.md             # BTC 历史上线事件
  3. patterns/listing_analysis.md               # 上线事件通用规律
  4. case_studies/by_event_type/listing/        # 历史上线案例
  5. learning_insights/successful_predictions.md # 成功预测经验

# 场景 2: 监管新闻 (regulation event)
→ Claude 自动读取:
  1. patterns/regulation_analysis.md            # 监管事件分析模式
  2. case_studies/by_event_type/regulatory_action/ # 历史监管案例
  3. market_patterns/price_reactions.md         # 价格反应模式
  4. learning_insights/confidence_calibration.md # 置信度校准经验

# 场景 3: 黑客攻击 (hack event)
→ Claude 自动读取:
  1. assets/{ASSET}/hack_analysis.md            # 该资产历史安全事件
  2. patterns/hack_analysis.md                  # 黑客攻击分析模式
  3. case_studies/by_event_type/security_breach/ # 历史安全案例
  4. learning_insights/failed_predictions.md    # 失败案例反思
```

#### 3.2.3 记忆检索辅助函数

```python
class ClaudeDeepAnalysisMemoryManager:
    """Claude CLI 专用记忆管理系统"""

    def __init__(self, base_path="./memories/claude_cli_deep_analysis"):
        self.memory_tool = MemoryToolHandler(base_path)
        self.analysis_categories = [
            "hack_analysis",       # 黑客攻击
            "regulation_analysis", # 监管事件
            "partnership_analysis",# 合作事件
            "listing_analysis",    # 上线事件
            "market_analysis",     # 市场走势
            "technical_analysis",  # 技术更新
        ]

    async def retrieve_similar_analyses(
        self,
        asset: str,
        event_type: str,
        limit: int = 3
    ) -> list[dict]:
        """检索相似的历史分析案例"""
        results = []

        # 1. 读取资产特定的分析历史
        asset_path = f"assets/{asset}/{event_type}_analysis.md"
        asset_memory = self.memory_tool.execute_tool_use({
            "command": "view",
            "path": asset_path
        })
        if asset_memory.get("success"):
            results.append({
                "type": "asset_specific",
                "content": asset_memory.get("content")
            })

        # 2. 读取事件类型通用规律
        pattern_path = f"patterns/{event_type}_analysis.md"
        pattern_memory = self.memory_tool.execute_tool_use({
            "command": "view",
            "path": pattern_path
        })
        if pattern_memory.get("success"):
            results.append({
                "type": "pattern",
                "content": pattern_memory.get("content")
            })

        # 3. 读取历史案例研究
        case_studies_path = f"case_studies/by_event_type/{event_type}/"
        case_studies = self.memory_tool.execute_tool_use({
            "command": "view",
            "path": case_studies_path
        })
        if case_studies.get("success"):
            results.append({
                "type": "case_studies",
                "content": case_studies.get("content")
            })

        return results[:limit]

    async def store_analysis_memory(
        self,
        asset: str,
        event_type: str,
        analysis_data: dict
    ):
        """存储深度分析记忆"""
        # 更新资产特定的分析记录
        asset_path = f"assets/{asset}/{event_type}_analysis.md"
        memory_entry = self._format_memory_entry(analysis_data)

        # 追加到文件末尾
        self.memory_tool.execute_tool_use({
            "command": "insert",
            "path": asset_path,
            "insert_line": -1,  # 末尾插入
            "insert_text": memory_entry
        })

    async def update_analysis_insights(
        self,
        insight_type: str,
        insights: str
    ):
        """更新分析洞察"""
        insight_path = f"learning_insights/{insight_type}.md"
        self.memory_tool.execute_tool_use({
            "command": "str_replace",
            "path": insight_path,
            "old_str": "## 最新洞察",
            "new_str": f"## 最新洞察\n\n{insights}"
        })

    def _format_memory_entry(self, data: dict) -> str:
        """格式化记忆条目"""
        return f"""
## {data.get('timestamp', 'Unknown')}: {data.get('event_summary', 'Event')}
- **初步分析**: confidence={data.get('preliminary_confidence')}, action={data.get('preliminary_action')}
- **深度分析调整**: confidence={data.get('final_confidence')}, reason="{data.get('adjustment_reason')}"
- **验证结果**: {data.get('verification_summary')}
- **关键洞察**: {data.get('key_insights')}
- **改进建议**: {data.get('improvement_suggestions')}

"""
```

#### 3.2.4 增量更新与记忆工具使用指南

分析完成后，Claude 会根据以下指南更新记忆：

```markdown
# Claude CLI 记忆工具使用指南

## 1. 查看记忆 - 在分析开始前（必须）
command: view
path: /memories/claude_cli_deep_analysis/{category}/{asset_or_event}

示例：
- 查看 BTC 上线分析历史: path: "assets/BTC/listing_analysis.md"
- 查看监管事件规律: path: "patterns/regulation_analysis.md"
- 查看历史案例: path: "case_studies/by_event_type/security_breach/"

## 2. 存储分析洞察 - 在分析完成后
command: create
path: /memories/claude_cli_deep_analysis/case_studies/{category}/{date}_{asset}_{event_type}.md
file_text: |
  # {Event Summary}

  ## 事件背景
  {描述}

  ## 初步分析
  - confidence: {value}
  - action: {action}
  - 理由: {reason}

  ## 深度分析调整
  - 调整后 confidence: {value}
  - 调整理由: {reason}
  - 工具验证: {tool_results}

  ## 实际结果
  - 价格变化: {percentage}
  - 是否符合预期: {yes/no}

  ## 关键洞察
  {insights}

  ## 改进建议
  {suggestions}

## 3. 更新资产档案 - 当有重要发现时
command: str_replace
path: /memories/claude_cli_deep_analysis/assets/{asset}/{event_type}_analysis.md
old_str: "## 最新分析\n\n"
new_str: "## 最新分析\n\n### {DATE}: {EVENT}\n- **结论**: {conclusion}\n\n"

## 4. 记录市场规律 - 当发现新规律时
command: insert
path: /memories/claude_cli_deep_analysis/patterns/{event_type}_analysis.md
insert_line: -1
insert_text: |

  ### {Pattern Name}
  - **触发条件**: {conditions}
  - **历史案例**: {examples}
  - **置信度调整**: {adjustment}
  - **Risk flags**: {flags}

## 5. 更新学习洞察 - 记录成功/失败案例
command: str_replace
path: /memories/claude_cli_deep_analysis/learning_insights/successful_predictions.md
old_str: "# 成功预测案例\n\n"
new_str: "# 成功预测案例\n\n## {DATE}: {Asset} - {Event}\n- **预测**: {prediction}\n- **结果**: {outcome}\n- **关键因素**: {factors}\n\n"
```

**示例：更新 BTC 上线事件分析记忆**

```python
# 分析完成后，Claude 自动执行：

# 1. 追加到资产特定分析记录
memory_tool_use({
    "command": "insert",
    "path": "assets/BTC/listing_analysis.md",
    "insert_line": -1,
    "insert_text": """
## 2025-01-23: Coinbase BTC 期货上线分析
- **初步信号**: confidence 0.8, action=buy, direction=long
- **工具验证**:
  - 搜索工具确认: 5 条来源，官方确认 ✅
  - 价格工具: BTC $107,817 (-0.68% 24h)
- **深度分析调整**: confidence → 0.6 (缺少成交量数据)
- **实际结果**: 24h 涨幅 +2.3%，符合预期
- **规律总结**: 交易所期货上线需观察首日成交量，无数据时保守评估
- **置信度校准**: 此类消息默认 confidence ≤0.6
"""
})

# 2. 更新通用规律库
memory_tool_use({
    "command": "str_replace",
    "path": "patterns/listing_analysis.md",
    "old_str": "## 期货上线分析\n\n",
    "new_str": """## 期货上线分析

### 置信度判断标准（更新: 2025-01-23）
- 有首日成交量数据 → confidence 可达 0.7-0.8
- 无成交量数据 → confidence ≤0.6，action=observe
- 建议等待数据确认后再决策

"""
})

# 3. 记录成功案例到学习洞察
memory_tool_use({
    "command": "insert",
    "path": "learning_insights/successful_predictions.md",
    "insert_line": -1,
    "insert_text": """
## 2025-01-23: BTC 期货上线 - 保守评估成功
- **预测**: confidence 0.6, action=observe（因缺成交量数据）
- **结果**: 24h 涨幅 +2.3%，符合预期但涨幅有限
- **关键决策**: 工具验证确认消息真实，但因数据不足保持保守
- **经验**: 上线类消息需区分"公告"与"实际成交"，无数据时保守正确
"""
})
```

#### 3.2.5 定期清理
```python
# 自动清理策略
- 删除 30 天前的 context/session_state.md
- 归档 90 天前的 case_studies/ 到 case_studies/archive/
- 合并重复的 patterns/ 文件
- 压缩超过 50KB 的资产档案
- 清理 learning_insights/ 中的过时案例
```

---

## 4. 数据模型

### 4.1 资产档案模板 (assets/_template.md)

```markdown
# {ASSET_NAME} 资产档案

## 基本信息
- **代码**: {ASSET_CODE}
- **全名**: {FULL_NAME}
- **类别**: Layer1 / DeFi / Meme / Infrastructure
- **首次分析**: {DATE}
- **最后更新**: {DATE}

## 历史分析记录
### {DATE}: {EVENT_TYPE}
- **事件**: {简短描述}
- **初步分析**: confidence={VALUE}, action={ACTION}
- **深度分析调整**: {调整原因}
- **实际结果**: {24h/7d 价格变化}
- **规律总结**: {关键洞察}

## 风险特征
- 流动性: 高/中/低
- 监管风险: 高/中/低
- 市场情绪: 热/温/冷

## 置信度校准
- 上线消息: 默认 {VALUE}
- 融资消息: 默认 {VALUE}
- 技术升级: 默认 {VALUE}
```

### 4.2 市场规律模板 (patterns/_template.md)

```markdown
# {规律名称}

## 定义
{清晰的规律描述}

## 触发条件
- 事件类型: {event_type}
- 资产特征: {asset_category}
- 市场环境: {bull/bear/sideways}

## 历史案例
### {DATE}: {ASSET} - {EVENT}
- **信号**: {初步判断}
- **调整**: {深度分析修正}
- **结果**: {实际价格表现}

## 应用规则
- 置信度调整: {+-X%}
- Risk flags: {需要添加的标记}
- Notes 模板: {标准说明文案}

## 最后更新
{DATE}
```

### 4.3 分析规则模板 (analysis_rules/)

```markdown
# 置信度校准规则

## 高置信度 (0.75-1.0) 触发条件
- [ ] 明确的时间线（具体日期/时间）
- [ ] 可验证的数据支撑（成交量、资金费率、链上指标）
- [ ] 权威来源（官方公告、主流交易所）
- [ ] 历史规律验证（有 3+ 相似案例支撑）

## 中置信度 (0.5-0.75) 触发条件
- [ ] 部分数据缺失但事件真实
- [ ] 时间线相对明确（本周/本月）
- [ ] 来源可信但非官方

## 低置信度 (0.0-0.5) 触发条件
- [x] 时间线模糊（"即将"、"不久"）
- [x] 缺乏实质内容（"大事件"、"重要更新"）
- [x] 无法验证的声明
- [x] 高风险投机（Meme 币、rug pull）
- [x] 仅社交媒体传闻

## 自动降级规则
- 检测到 vague_timeline → confidence ≤0.5
- 检测到 speculative → confidence ≤0.4
- 检测到 unverifiable → confidence ≤0.4
- 24h 涨幅 <1% 且新闻报道"突破" → confidence ≤0.5, action=observe
```

---

## 5. 安全设计

### 5.1 路径验证（防止目录穿越攻击）**【强制要求】**

**官方安全要求**: 必须严格验证所有路径以防止目录穿越攻击。恶意路径输入可能尝试访问 `/memories` 目录之外的文件。

```python
def _validate_path(self, path: str) -> Path:
    """
    验证路径安全性（官方要求的强制措施）

    防御措施:
    1. 验证路径以 /memories 开头
    2. 解析为规范形式（canonical form）
    3. 拒绝遍历模式：../, ..\, URL 编码序列
    """
    # 移除前导斜杠
    path = path.lstrip("/")

    # 构造绝对路径
    base_path = Path("./memories/claude_cli_deep_analysis").resolve()
    full_path = (base_path / path).resolve()

    # 检查是否在 base_path 内（使用 Python pathlib.resolve()）
    if not full_path.is_relative_to(base_path):
        raise SecurityError(f"路径穿越攻击检测: {path}")

    # 额外检查：拒绝明显的遍历模式
    dangerous_patterns = ["../", "..\\", "%2e%2e%2f", "%2e%2e/", "..%2f"]
    path_lower = path.lower()
    for pattern in dangerous_patterns:
        if pattern in path_lower:
            raise SecurityError(f"检测到危险路径模式: {pattern}")

    return full_path
```

**官方建议的验证清单**:
- ✅ 所有路径以 `/memories` 开头
- ✅ 使用语言内置工具解析为规范形式（Python 的 `pathlib.Path.resolve()`）
- ✅ 拒绝 `../`, `..\`, URL 编码的遍历序列（`%2e%2e%2f` 等）
- ✅ 在生产环境中记录所有路径验证失败的审计日志

### 5.2 内容审查（防止 Prompt Injection 和敏感信息泄露）

**官方建议**: Claude 通常会拒绝写入敏感信息，但应用程序应实施更严格的验证和过滤。

```python
def _sanitize_content(self, content: str) -> str:
    """
    过滤危险模式和敏感信息（官方推荐的额外保护）

    官方警告: 不要依赖 Claude 的默认过滤，应实施更严格的验证
    """
    # 1. 过滤 Prompt Injection 模式
    dangerous_patterns = [
        r"<\|.*?\|>",              # Special tokens
        r"```.*?system.*?```",     # System prompt injection
        r"ignore\s+previous",      # Instruction override
        r"disregard\s+all",
    ]

    sanitized = content
    for pattern in dangerous_patterns:
        sanitized = re.sub(pattern, "[filtered]", sanitized, flags=re.IGNORECASE)

    # 2. 过滤敏感信息（官方建议的额外保护）
    sensitive_patterns = [
        (r"[A-Za-z0-9]{40,}", "[API_KEY_FILTERED]"),     # API keys
        (r"sk-[A-Za-z0-9]{40,}", "[SK_FILTERED]"),       # OpenAI keys
        (r"\b\d{16}\b", "[CARD_FILTERED]"),              # Credit card numbers
        (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[EMAIL_FILTERED]"),  # Emails
    ]

    for pattern, replacement in sensitive_patterns:
        sanitized = re.sub(pattern, replacement, sanitized)

    # 3. 限制长度（官方建议的文件大小控制）
    if len(sanitized) > 50000:
        sanitized = sanitized[:50000] + "\n\n[truncated]"

    return sanitized
```

### 5.3 文件大小限制（官方建议）

**官方建议**: 监控文件大小，防止过度增长。实施最大字符限制和分页。

```python
# 单个文件最大 50KB（官方建议范围）
MAX_FILE_SIZE = 50 * 1024

# 总记忆目录最大 10MB
MAX_TOTAL_SIZE = 10 * 1024 * 1024

# 单次 view 命令最多返回 500 行（官方建议的分页）
MAX_VIEW_LINES = 500

def enforce_file_size_limit(file_path: Path, max_size: int = MAX_FILE_SIZE):
    """强制执行文件大小限制（官方推荐）"""
    if file_path.stat().st_size > max_size:
        raise ValueError(f"文件超过大小限制: {file_path.name} ({file_path.stat().st_size} bytes)")

def check_total_memory_size(base_path: Path, max_total: int = MAX_TOTAL_SIZE):
    """检查总记忆目录大小（官方推荐）"""
    total_size = sum(f.stat().st_size for f in base_path.rglob("*") if f.is_file())
    if total_size > max_total:
        logger.warning(f"记忆目录超过大小限制: {total_size / 1024 / 1024:.2f}MB")
        return False
    return True
```

### 5.4 过期策略（官方建议）

**官方建议**: 定期清理长期未使用的记忆文件，实施过期策略。

```python
def implement_expiration_policy(base_path: Path, days_threshold: int = 90):
    """
    实施记忆文件过期策略（官方推荐）

    官方建议: 定期清理未使用的文件，防止记忆膨胀
    """
    from datetime import datetime, timedelta

    cutoff_date = datetime.now() - timedelta(days=days_threshold)

    expired_files = []
    for file_path in base_path.rglob("*.md"):
        last_modified = datetime.fromtimestamp(file_path.stat().st_mtime)
        if last_modified < cutoff_date:
            expired_files.append(file_path)

    # 归档而非直接删除（保留历史）
    archive_path = base_path / "archive"
    archive_path.mkdir(exist_ok=True)

    for file_path in expired_files:
        archive_dest = archive_path / file_path.name
        file_path.rename(archive_dest)
        logger.info(f"归档过期记忆文件: {file_path.name} (last modified: {last_modified})")

    return len(expired_files)
```

---

## 6. 使用场景示例

### 6.1 场景 1: BTC ETF 上线分析

```
用户消息: "Coinbase 宣布 BTC ETF 期权上线"

→ 主 AI 分析 (Gemini Flash):
  - confidence: 0.82
  - action: buy
  - event_type: listing

→ 触发深度分析 (Claude + Memory Tool):

  [1] Claude 启动检查:
      view /memories/claude_deep_analysis/
      → 发现 assets/BTC.md, patterns/listing_effect.md

  [2] 读取相关记忆:
      view /memories/claude_deep_analysis/assets/BTC.md
      view /memories/claude_deep_analysis/patterns/listing_effect.md

  [3] 深度分析输出:
      - confidence: 0.82 → 0.65 (缺少首日成交量数据)
      - risk_flags: + data_incomplete
      - notes: "期权上线需观察首日成交量，建议等待数据确认"

  [4] 更新记忆:
      str_replace /memories/claude_deep_analysis/assets/BTC.md
      → 追加本次分析记录和规律总结
```

### 6.2 场景 2: 未知 Meme 币暴富消息

```
用户消息: "某巨鲸买入神秘 Meme 币 $PEPE2，24h 涨 1000%"

→ 主 AI 分析 (Gemini Flash):
  - confidence: 0.78
  - action: buy
  - event_type: whale

→ 触发深度分析 (Claude + Memory Tool):

  [1] Claude 启动检查:
      view /memories/claude_deep_analysis/patterns/whale_behavior.md
      view /memories/claude_deep_analysis/analysis_rules/signal_quality.md

  [2] 读取风险规则:
      → 发现 "Meme 币暴富消息默认 confidence ≤0.3"
      → 发现 "无链上验证数据 → 添加 unverifiable"

  [3] 深度分析输出:
      - confidence: 0.78 → 0.25 (高风险投机)
      - event_type: whale → scam_alert
      - action: buy → observe
      - risk_flags: + speculative, liquidity_risk, unverifiable
      - notes: "Meme 币暴富传闻，缺乏链上验证，高度投机，不建议跟进"

  [4] 更新规律库:
      str_replace /memories/claude_deep_analysis/patterns/whale_behavior.md
      → 追加 "Meme 币巨鲸消息需谨慎，默认低置信度"
```

### 6.3 场景 3: 价格反弹误判

```
用户消息: "BTC 突破 $50k，开启新一轮上涨"

价格工具数据:
- 1h: +0.8%
- 24h: -0.2%
- 7d: -3.5%

→ 主 AI 分析 (Gemini Flash):
  - confidence: 0.85
  - action: buy
  - direction: long

→ 触发深度分析 (Claude + Memory Tool):

  [1] 读取规则:
      view /memories/claude_deep_analysis/patterns/false_breakout.md
      → 发现 "24h 涨幅 <1% 且新闻报道突破 → 可能是假突破"

  [2] 深度分析输出:
      - confidence: 0.85 → 0.45
      - action: buy → observe
      - direction: long → neutral
      - risk_flags: + vague_timeline, speculative
      - notes: "短线反弹，24h 涨幅仅 +0.8%，7d 仍跌 -3.5%，未扭转趋势，警惕假突破"

  [3] 更新规律:
      str_replace /memories/claude_deep_analysis/patterns/false_breakout.md
      → 追加 "价格反弹判断：需对比多时间周期（1h/24h/7d），单一周期为正不足以判断新趋势"
```

---

## 7. 配置管理

### 7.1 环境变量

```bash
# Claude CLI 深度分析记忆配置
CLAUDE_DEEP_MEMORY_ENABLED=true
CLAUDE_DEEP_MEMORY_BASE_PATH=./memories/claude_cli_deep_analysis
CLAUDE_DEEP_MEMORY_MAX_FILE_SIZE=51200          # 50KB
CLAUDE_DEEP_MEMORY_MAX_TOTAL_SIZE=10485760      # 10MB
CLAUDE_DEEP_MEMORY_AUTO_CLEANUP=true
CLAUDE_DEEP_MEMORY_CLEANUP_DAYS=30              # 删除 30 天前的文件

# Claude API 配置
CLAUDE_API_KEY=your-api-key
CLAUDE_MODEL=claude-sonnet-4-5-20250929
CLAUDE_MAX_TOOL_TURNS=3                         # 最多 3 轮工具调用

# Context Management
CLAUDE_CONTEXT_TRIGGER_TOKENS=10000
CLAUDE_CONTEXT_KEEP_TOOLS=2
CLAUDE_CONTEXT_CLEAR_AT_LEAST=500
```

### 7.2 初始化流程

```python
# 在 src/ai/deep_analysis/claude.py 中初始化

from src.memory.claude_deep_memory_handler import ClaudeDeepAnalysisMemoryHandler

def create_claude_deep_analysis_engine(config):
    """创建 Claude 深度分析引擎（带独立记忆）"""

    # 1. 创建独立的深度分析记忆处理器
    memory_enabled = config.get("CLAUDE_DEEP_MEMORY_ENABLED", True)
    memory_handler = None

    if memory_enabled:
        memory_handler = ClaudeDeepAnalysisMemoryHandler(
            base_path=config.get("CLAUDE_DEEP_MEMORY_BASE_PATH", "./memories/claude_cli_deep_analysis"),
            max_file_size=config.get("CLAUDE_DEEP_MEMORY_MAX_FILE_SIZE", 51200),
            auto_cleanup=config.get("CLAUDE_DEEP_MEMORY_AUTO_CLEANUP", True),
            cleanup_days=config.get("CLAUDE_DEEP_MEMORY_CLEANUP_DAYS", 30),
        )
        logger.info("Claude 深度分析记忆系统已启用")

    # 2. 创建 Anthropic 客户端（集成记忆处理器）
    client = AnthropicClient(
        api_key=config["CLAUDE_API_KEY"],
        model_name=config["CLAUDE_MODEL"],
        memory_handler=memory_handler,  # 传入独立的记忆处理器
        max_tool_turns=config.get("CLAUDE_MAX_TOOL_TURNS", 3),
        context_trigger_tokens=config.get("CLAUDE_CONTEXT_TRIGGER_TOKENS", 10000),
    )

    # 3. 创建深度分析引擎
    engine = ClaudeDeepAnalysisEngine(
        client=client,
        parse_json_callback=parse_signal_json,
    )

    return engine
```

---

## 8. 监控与维护

### 8.1 关键指标

```python
# 记忆系统健康指标
metrics = {
    "total_files": 42,                    # 总文件数
    "total_size_mb": 3.2,                 # 总大小（MB）
    "assets_count": 15,                   # 资产档案数量
    "patterns_count": 8,                  # 规律数量
    "events_count": 19,                   # 历史事件数量
    "last_cleanup": "2025-01-22",         # 最后清理时间
    "memory_tool_calls": 156,             # 工具调用次数
    "avg_calls_per_analysis": 3.2,        # 平均每次分析调用次数
}
```

### 8.2 日志记录

```python
# 审计日志示例
logger.info("Claude Memory Tool 调用: view /memories/claude_cli_deep_analysis/assets/BTC.md")
logger.warning("Claude Memory 写入: assets/BTC.md | 新增分析记录 2025-01-23")
logger.info("Claude Memory 更新: patterns/listing_effect.md | 追加规律总结")
logger.warning("Claude Memory 清理: 删除 30 天前的 15 个文件")
```

### 8.3 定期维护任务

```python
# 每日维护任务
- 检查记忆目录总大小（不超过 10MB）
- 清理过期的 context/ 文件（30 天）
- 归档旧的 events/ 文件（90 天）

# 每周维护任务
- 合并重复的 patterns/ 文件
- 压缩超大的 assets/ 档案（>50KB）
- 生成记忆系统健康报告

# 每月维护任务
- 审查所有 analysis_rules/ 的有效性
- 更新 README.md 使用指南
- 备份整个记忆目录到 Supabase
```

---

## 9. 迁移与回滚

### 9.1 启用 Claude 深度分析记忆

```bash
# 1. 设置环境变量
export CLAUDE_DEEP_MEMORY_ENABLED=true

# 2. 重启服务
npm run restart

# 3. 检查日志
npm run logs -- --lines 50 | grep "Claude Memory"
```

### 9.2 禁用记忆（降级到无记忆模式）

```bash
# 1. 禁用环境变量
export CLAUDE_DEEP_MEMORY_ENABLED=false

# 2. 重启服务
npm run restart

# 深度分析仍然工作，但不使用 Memory Tool
```

### 9.3 备份与恢复

```bash
# 备份记忆目录
tar -czf claude_cli_deep_memory_backup_$(date +%Y%m%d).tar.gz ./memories/claude_cli_deep_analysis/

# 恢复记忆目录
tar -xzf claude_deep_memory_backup_20250123.tar.gz
```

---

## 10. 最佳实践

### 10.1 记忆文件命名规范

**官方建议**: 使用描述性文件名和适当的文件扩展名（.xml, .md, .txt）以提高组织性和检索效率。

```
✅ 好的命名（遵循官方建议）:
- assets/BTC/profile.md                        # 清晰的层级 + 描述性名称
- assets/BTC/listing_analysis.md               # 按事件类型分类
- patterns/hack_analysis.md                    # 领域特定的清晰命名
- case_studies/by_event_type/security_breach/2024-binance-hack.md  # 时间 + 描述
- learning_insights/successful_predictions.md  # 明确的用途说明

❌ 不好的命名:
- assets/bitcoin.md                            # 应使用标准代码 BTC
- patterns/rule1.md                            # 缺乏语义
- case_studies/event_123.md                    # 缺乏时间和类型信息
- temp.md                                      # 模糊的临时文件
- data.txt                                     # 无法识别内容
```

**命名最佳实践**:
- 使用领域特定的清晰命名（如 `hack_analysis.md`, `regulation_analysis.md`）
- 包含时间信息用于历史追踪（如 `2025-01-btc-etf.md`）
- 使用适当的文件扩展名（.md 用于 Markdown, .xml 用于结构化数据）
- 避免通用或模糊的名称（如 `file1.md`, `notes.txt`）
```

### 10.2 记忆内容组织

**官方建议**: 保持记忆内容"up-to-date, coherent and organized"（最新、连贯、有组织），定期重命名或删除不再相关的文件。

```markdown
✅ 结构化、可解析（官方推荐）:
# BTC 资产档案

## 基本信息
- 代码: BTC
- 类别: Layer1
- 最后更新: 2025-01-23

## 最新分析
### 2025-01-23: listing - Coinbase 期货上线
- **初步**: confidence 0.8, buy
- **深度**: confidence 0.6, observe (缺少成交量)
- **工具验证**: 搜索工具确认官方公告 ✅
- **结果**: +2.3%
- **规律**: 期货上线需等待首日成交量
- **改进**: 下次立即调用价格工具获取成交量数据

❌ 非结构化、难维护（违反官方建议）:
昨天分析了 BTC，好像涨了，下次注意一下成交量。
```

**内容最佳实践**:
- **Selective storage**: 只存储与任务直接相关的信息，不要保留一切
- **Progressive updates**: 在工作进行时持续记录进度和想法，而非等到完成
- **Content governance**: 避免存储敏感信息（API keys, 私钥等）
- **Maintenance prompting**: 在 System Prompt 中指导 Claude "只记录与[特定主题]相关的信息"

### 10.3 避免记忆膨胀（官方警告）

**官方警告**: 避免创建杂乱的记忆文件夹，定期审查和清理未使用的条目。

```python
# 定期审查和精简记忆（遵循官方建议）
- ✅ 删除不再有效的规律
- ✅ 合并相似的资产档案
- ✅ 归档历史案例（只保留最近 90 天）
- ✅ 压缩重复的分析记录
- ✅ 监控文件大小，实施最大字符限制
- ✅ 实施过期策略，定期清理长期未使用的文件
```

**防止膨胀的自动化策略**:
```python
# 自动清理脚本
import os
from datetime import datetime, timedelta
from pathlib import Path

def cleanup_old_memories(base_path, days=90):
    """清理超过指定天数的记忆文件"""
    cutoff = datetime.now() - timedelta(days=days)

    for filepath in Path(base_path).rglob("*.md"):
        if filepath.stat().st_mtime < cutoff.timestamp():
            # 归档或删除
            archive_path = base_path / "archive" / filepath.name
            filepath.rename(archive_path)

def consolidate_duplicates(base_path):
    """合并重复内容的记忆文件"""
    # 检测相似内容并合并
    pass

def enforce_size_limits(base_path, max_size_kb=50):
    """强制执行文件大小限制"""
    for filepath in Path(base_path).rglob("*.md"):
        size_kb = filepath.stat().st_size / 1024
        if size_kb > max_size_kb:
            # 压缩或分割文件
            print(f"警告: {filepath} 超过大小限制 ({size_kb:.1f}KB)")
```

---

## 11. 故障排查

### 11.1 常见问题

**Q: Claude 没有自动读取记忆？**
```
A: 检查:
1. CLAUDE_DEEP_MEMORY_ENABLED=true
2. memory_handler 正确传入 AnthropicClient
3. betas=["context-management-2025-06-27"] 已设置
4. 记忆目录存在且有内容
```

**Q: 记忆文件过大导致响应缓慢？**
```
A: 优化策略:
1. 限制单个文件 ≤50KB
2. 分割大型资产档案
3. 使用摘要代替完整历史
4. 定期归档旧事件
```

**Q: 记忆内容被错误修改？**
```
A: 安全措施:
1. 启用审计日志（所有写操作）
2. 定期备份记忆目录
3. 审查 Claude 的修改建议
4. 使用版本控制（Git）追踪变化
```

---

## 12. 未来优化方向

1. **向量检索增强**: 结合 Supabase 向量搜索，自动推荐相关记忆文件
2. **记忆摘要生成**: 自动生成资产档案和规律的摘要，加速检索
3. **跨引擎记忆共享**: 让 Gemini 深度分析也能读取 Claude 的记忆洞察
4. **智能过期策略**: 根据记忆使用频率动态调整清理时间
5. **可视化仪表盘**: 展示记忆系统的健康状态和关键规律

---

## 13. 参考资料

- [Anthropic Memory Tool 官方文档](https://docs.claude.com/en/docs/agents-and-tools/tool-use/memory-tool)
- [Claude Context Management](https://docs.anthropic.com/en/docs/build-with-claude/context-management)
- [tg-crypto-listener 深度分析架构](./deep_analysis_engine_switch_plan.md)
- [通用记忆系统设计](./memory_architecture.md)
