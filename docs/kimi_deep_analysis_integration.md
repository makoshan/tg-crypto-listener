# Kimi 深度分析集成方案

## 概述

Kimi 开放平台提供 OpenAI 兼容 API，可以像 Qwen 一样直接集成到现有的深度分析系统中。本文档说明如何快速集成 Kimi。

## 架构分析

### 现有架构

1. **深度分析引擎抽象** (`src/ai/deep_analysis/base.py`)
   - `DeepAnalysisEngine` 抽象基类
   - `analyse()` 方法执行深度分析

2. **OpenAI 兼容引擎** (`src/ai/deep_analysis/openai_compatible.py`)
   - 支持 Qwen、OpenAI、DeepSeek 等 OpenAI 兼容 API
   - 支持 Function Calling 工具调用
   - 工具执行逻辑在 `_execute_tool()` 中

3. **工厂模式** (`src/ai/deep_analysis/factory.py`)
   - `create_deep_analysis_engine()` 根据 provider 创建引擎

### Kimi 特点

1. **OpenAI 兼容 API**
   - Base URL: `https://api.moonshot.cn/v1`
   - 使用标准 OpenAI SDK 调用
   - **与 Qwen 实现方式完全一致**

2. **官方工具（Formula）**（可选，当前方案不使用）
   - 通过 Formula URI 调用（如 `moonshot/web-search:latest`）
   - 需要特殊处理（未来可扩展）
   - 当前方案使用项目自带的工具（search_news, get_price 等）

## 实现方案（方案 A：简单方案，推荐）

### 核心思路

**Kimi 与 Qwen 的实现方式完全一样**，只需要：
1. 在 `config.py` 中添加 Kimi 配置
2. 在 `factory.py` 中添加 `kimi` provider 支持
3. **不需要创建新的 `kimi.py` 文件**

### 实现步骤

#### 1. 配置扩展 (`src/config.py`)

在 `Config` 类中添加：

```python
# ==============================================
# Kimi Deep Analysis Configuration
# ==============================================
MOONSHOT_API_KEY: str = os.getenv("MOONSHOT_API_KEY", "")
MOONSHOT_BASE_URL: str = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_DEEP_MODEL: str = os.getenv("KIMI_DEEP_MODEL", "kimi-k2-turbo-preview")
KIMI_DEEP_TIMEOUT_SECONDS: float = float(os.getenv("KIMI_DEEP_TIMEOUT_SECONDS", "30"))
KIMI_DEEP_MAX_FUNCTION_TURNS: int = int(os.getenv("KIMI_DEEP_MAX_FUNCTION_TURNS", "6"))
```

在 `get_deep_analysis_config()` 方法中添加：

```python
"kimi": {
    "api_key": cls.MOONSHOT_API_KEY,
    "base_url": cls.MOONSHOT_BASE_URL,
    "model": cls.KIMI_DEEP_MODEL,
    "timeout": cls.KIMI_DEEP_TIMEOUT_SECONDS,
    "max_function_turns": cls.KIMI_DEEP_MAX_FUNCTION_TURNS,
},
```

在 `get_deep_analysis_config()` 的 `allowed_providers` 中添加 `"kimi"`：

```python
allowed_providers = {
    "claude",
    "gemini",
    "minimax",
    "codex_cli",
    "claude_cli",
    "qwen",
    "openai",
    "deepseek",
    "kimi",  # 新增
}
```

#### 2. 工厂函数扩展 (`src/ai/deep_analysis/factory.py`)

在 `create_deep_analysis_engine()` 方法中，找到处理 `qwen, openai, deepseek` 的代码块，修改为：

```python
# OpenAI Compatible API (Qwen, OpenAI, DeepSeek, Kimi)
if provider in ["qwen", "openai", "deepseek", "kimi"]:
    logger.info(f"🔧 开始初始化 {provider.upper()} 深度分析引擎...")

    # Get provider-specific config
    provider_cfg = deep_config.get(provider, {})

    # API Key
    if provider == "qwen":
        api_key_attr = "DASHSCOPE_API_KEY"
    elif provider == "kimi":
        api_key_attr = "MOONSHOT_API_KEY"
    else:
        api_key_attr = f"{provider.upper()}_API_KEY"
    
    api_key = provider_cfg.get("api_key") or getattr(config, api_key_attr, "")
    if not api_key:
        raise DeepAnalysisError(f"{provider.upper()} API key 未配置，无法启用深度分析")

    # Base URL
    base_url_map = {
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "kimi": "https://api.moonshot.cn/v1",
    }
    base_url_attr = f"{provider.upper()}_BASE_URL"
    base_url = provider_cfg.get("base_url") or getattr(config, base_url_attr, base_url_map[provider])

    # Model
    model_map = {
        "qwen": "qwen-plus",
        "openai": "gpt-4-turbo",
        "deepseek": "deepseek-chat",
        "kimi": "kimi-k2-turbo-preview",
    }
    model_attr = f"{provider.upper()}_DEEP_MODEL"
    model = provider_cfg.get("model") or getattr(config, model_attr, model_map[provider])

    # Enable search (Qwen specific)
    enable_search = False
    if provider == "qwen":
        enable_search = provider_cfg.get("enable_search") or getattr(config, "QWEN_ENABLE_SEARCH", False)
        model = _normalise_openai_compatible_model(provider, model)

    # Timeout
    timeout_attr = f"{provider.upper()}_DEEP_TIMEOUT_SECONDS"
    timeout = float(provider_cfg.get("timeout") or getattr(config, timeout_attr, 30.0))

    # Max function turns
    max_turns_attr = f"{provider.upper()}_DEEP_MAX_FUNCTION_TURNS"
    max_turns = int(provider_cfg.get("max_function_turns") or getattr(config, max_turns_attr, 6))

    logger.info(
        f"🧠 {provider.upper()} 深度分析引擎已初始化: "
        f"model={model}, enable_search={enable_search}, max_turns={max_turns}"
    )

    return OpenAICompatibleEngine(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        enable_search=enable_search,
        timeout=timeout,
        max_function_turns=max_turns,
        parse_json_callback=parse_callback,
        memory_bundle=memory_bundle,
        config=config,
    )
```

## 使用示例

### 环境变量配置

在 `.env` 文件中添加：

```bash
# 启用 Kimi 深度分析
DEEP_ANALYSIS_ENABLED=true
DEEP_ANALYSIS_PROVIDER=kimi

# Kimi API 配置
MOONSHOT_API_KEY=sk-xxx
MOONSHOT_BASE_URL=https://api.moonshot.cn/v1
KIMI_DEEP_MODEL=kimi-k2-turbo-preview

# 可选：超时和最大工具调用次数
KIMI_DEEP_TIMEOUT_SECONDS=30
KIMI_DEEP_MAX_FUNCTION_TURNS=6
```

### 工具支持

Kimi 引擎可以使用项目自带的工具：
- `search_news`: 搜索工具（如果 `TOOL_SEARCH_ENABLED=true`）
- `get_price`: 价格工具（如果 `TOOL_PRICE_ENABLED=true`）
- `get_macro_data`: 宏观工具（如果 `TOOL_MACRO_ENABLED=true`）
- `get_onchain_data`: 链上工具（如果 `TOOL_ONCHAIN_ENABLED=true`）
- `get_protocol_data`: 协议工具（如果 `TOOL_PROTOCOL_ENABLED=true`）

## 与 Qwen 的对比

| 特性 | Qwen | Kimi |
|------|------|------|
| API 兼容性 | OpenAI 兼容 | OpenAI 兼容 |
| Base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `https://api.moonshot.cn/v1` |
| 特殊参数 | `enable_search` | 无 |
| 工具支持 | 项目自带工具 | 项目自带工具 |
| 实现方式 | `OpenAICompatibleEngine` | `OpenAICompatibleEngine` |

**结论**：Kimi 与 Qwen 的实现方式**完全一致**，只是配置不同。

## 代码改动总结

### 需要修改的文件

1. **`src/config.py`**
   - 添加 5 个配置项（MOONSHOT_API_KEY, MOONSHOT_BASE_URL, KIMI_DEEP_MODEL, KIMI_DEEP_TIMEOUT_SECONDS, KIMI_DEEP_MAX_FUNCTION_TURNS）
   - 在 `get_deep_analysis_config()` 中添加 `kimi` 配置
   - 在 `allowed_providers` 中添加 `"kimi"`

2. **`src/ai/deep_analysis/factory.py`**
   - 在 `provider in ["qwen", "openai", "deepseek"]` 中添加 `"kimi"`
   - 在 `base_url_map` 和 `model_map` 中添加 Kimi 的默认值
   - 在 API Key 获取逻辑中添加 Kimi 的特殊处理

### 不需要创建的文件

- ❌ **不需要** `src/ai/deep_analysis/kimi.py`（直接复用 `OpenAICompatibleEngine`）

## 测试验证

### 1. 配置验证

```bash
# 检查配置是否正确加载
python -c "from src.config import Config; print(Config.MOONSHOT_API_KEY)"
```

### 2. 引擎创建测试

```python
from src.ai.deep_analysis import create_deep_analysis_engine
from src.config import Config

engine = create_deep_analysis_engine(
    provider="kimi",
    config=Config,
    parse_callback=lambda x: None,
    memory_bundle=None,
)
print(f"✅ Kimi 引擎创建成功: {engine.provider}")
```

### 3. 完整流程测试

运行监听器，观察日志中是否出现：
```
🧠 KIMI 深度分析引擎已初始化: model=kimi-k2-turbo-preview, enable_search=False, max_turns=6
```

## 后续扩展（可选）

如果未来需要使用 Kimi 官方工具（Formula），可以：

1. 创建 `src/ai/deep_analysis/kimi.py`
2. 继承 `OpenAICompatibleEngine`
3. 添加 Formula 工具加载和执行逻辑
4. 在 `factory.py` 中使用 `KimiDeepAnalysisEngine` 替代 `OpenAICompatibleEngine`

详细实现方案可参考：
- Formula 工具加载：`GET /formulas/{uri}/tools`
- Formula 工具执行：`POST /formulas/{uri}/fibers`
- 结果处理：`encrypted_output` 或 `output` 字段

## 注意事项

1. **API Key 配置**
   - 使用 `MOONSHOT_API_KEY`（不是 `KIMI_API_KEY`）
   - 从 Kimi 开放平台获取

2. **模型选择**
   - 默认：`kimi-k2-turbo-preview`
   - 可通过 `KIMI_DEEP_MODEL` 环境变量修改

3. **工具支持**
   - 当前方案使用项目自带的工具
   - 如需使用 Kimi 官方工具，需要扩展实现

4. **与 Qwen 的区别**
   - Qwen 有 `enable_search` 参数（内置联网搜索）
   - Kimi 没有此参数
   - 其他完全一致

## 常见问题

### Q: 为什么不需要创建 `kimi.py`？

A: 因为 Kimi 与 Qwen 一样都是 OpenAI 兼容 API，可以直接使用 `OpenAICompatibleEngine`。只有需要特殊处理（如 Formula 工具）时才需要创建新类。

### Q: 可以使用 Kimi 的官方工具吗？

A: 当前方案不支持。如需使用，需要扩展实现（参考"后续扩展"章节）。

### Q: 与 Qwen 的性能对比如何？

A: 性能取决于模型和 API 响应速度，代码层面没有差异。

### Q: 如何切换回 Qwen？

A: 只需修改环境变量：
```bash
DEEP_ANALYSIS_PROVIDER=qwen
DASHSCOPE_API_KEY=xxx
```

## 总结

Kimi 集成非常简单，只需要：
1. ✅ 在 `config.py` 中添加配置（5 行）
2. ✅ 在 `factory.py` 中添加 provider 支持（修改 3 处）
3. ✅ 配置环境变量
4. ✅ 完成！

**总代码改动：约 20 行**
