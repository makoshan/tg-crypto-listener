# Kimi 深度分析设置指南

## 代码实现完成 ✅

Kimi 深度分析集成代码已实现，包含以下修改：

1. **`src/config.py`**
   - 添加了 5 个 Kimi 配置项
   - 在 `get_deep_analysis_config()` 中添加了 `kimi` 配置
   - 在 `allowed_providers` 中添加了 `"kimi"`

2. **`src/ai/deep_analysis/factory.py`**
   - 在 OpenAI 兼容 API 处理中添加了 `"kimi"`
   - 添加了 Kimi 的 API Key、Base URL、Model 配置

## 配置步骤

### 1. 设置环境变量

在 `.env` 文件中添加以下配置：

```bash
# 启用 Kimi 深度分析
DEEP_ANALYSIS_ENABLED=true
DEEP_ANALYSIS_PROVIDER=kimi

# Kimi API 配置（使用你提供的 API key）
MOONSHOT_API_KEY=sk-GMkkv7YuGUR1n8Yh4y6dPW1vvPvMfW3pzuKejJxSkJICl327
MOONSHOT_BASE_URL=https://api.moonshot.cn/v1
KIMI_DEEP_MODEL=kimi-k2-turbo-preview

# 可选配置
KIMI_DEEP_TIMEOUT_SECONDS=30
KIMI_DEEP_MAX_FUNCTION_TURNS=6
```

### 2. 验证配置

运行以下命令验证配置是否正确：

```bash
python3 -c "from src.config import Config; print('✅ Config 加载成功'); print(f'MOONSHOT_API_KEY: {\"已配置\" if Config.MOONSHOT_API_KEY else \"未配置\"}'); print(f'KIMI_DEEP_MODEL: {Config.KIMI_DEEP_MODEL}')"
```

### 3. 启动监听器

```bash
uvx --with-requirements requirements.txt python -m src.listener
```

## 功能说明

### 与 Qwen 完全一致

Kimi 的实现方式与 Qwen **完全一致**：
- 使用相同的 `OpenAICompatibleEngine`
- 支持项目自带的工具（search_news, get_price, get_macro_data 等）
- 工具调用逻辑完全相同

### 工具支持

Kimi 引擎可以使用以下工具（如果已启用）：
- `search_news`: 搜索工具（`TOOL_SEARCH_ENABLED=true`）
- `get_price`: 价格工具（`TOOL_PRICE_ENABLED=true`）
- `get_macro_data`: 宏观工具（`TOOL_MACRO_ENABLED=true`）
- `get_onchain_data`: 链上工具（`TOOL_ONCHAIN_ENABLED=true`）
- `get_protocol_data`: 协议工具（`TOOL_PROTOCOL_ENABLED=true`）

### 日志输出

启动后，你应该看到类似以下的日志：

```
🔧 开始初始化 KIMI 深度分析引擎...
🧠 KIMI 深度分析引擎已初始化: model=kimi-k2-turbo-preview, enable_search=False, max_turns=6
```

## 切换 Provider

如果需要切换回其他 provider，只需修改 `.env`：

```bash
# 切换到 Qwen
DEEP_ANALYSIS_PROVIDER=qwen
DASHSCOPE_API_KEY=xxx

# 切换到 Gemini
DEEP_ANALYSIS_PROVIDER=gemini
GEMINI_API_KEY=xxx

# 切换到 Claude
DEEP_ANALYSIS_PROVIDER=claude
CLAUDE_API_KEY=xxx
```

## 故障排查

### 1. API Key 未配置

**错误信息**：`KIMI API key 未配置，无法启用深度分析`

**解决方法**：确保 `.env` 文件中设置了 `MOONSHOT_API_KEY`

### 2. 未知的 Provider

**错误信息**：`未知的 DEEP_ANALYSIS_PROVIDER=kimi`

**解决方法**：检查 `src/config.py` 中的 `allowed_providers` 是否包含 `"kimi"`

### 3. 模块导入错误

**错误信息**：`ModuleNotFoundError: No module named 'openai'`

**解决方法**：安装依赖
```bash
uvx --with-requirements requirements.txt python -m pip install openai
```

## 后续扩展（可选）

如果未来需要使用 Kimi 官方工具（Formula），可以参考：
- `docs/kimi_deep_analysis_integration.md` 中的"后续扩展"章节
- 需要创建 `src/ai/deep_analysis/kimi.py` 并实现 Formula 工具支持

## 总结

✅ 代码已实现
✅ 配置已添加
✅ 与 Qwen 实现方式一致
✅ 支持项目自带工具

只需在 `.env` 中配置 `MOONSHOT_API_KEY` 即可使用！
