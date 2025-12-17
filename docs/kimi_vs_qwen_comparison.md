# Kimi vs Qwen 实现对比

## 相同点

1. **基础 API 调用**：都是 OpenAI 兼容 API
   - 使用 `AsyncOpenAI` 客户端
   - 调用 `chat.completions.create()`
   - 支持 Function Calling

2. **工具定义格式**：都使用 OpenAI Function Calling 格式
   ```json
   {
     "type": "function",
     "function": {
       "name": "tool_name",
       "description": "...",
       "parameters": {...}
     }
   }
   ```

## 关键差异

### Qwen 的实现

```python
# factory.py
return OpenAICompatibleEngine(
    provider="qwen",
    api_key=api_key,
    base_url=base_url,
    model=model,
    enable_search=True,  # 唯一特殊点：通过 extra_body 传递
    ...
)
```

**工具执行方式**：
- 工具定义在代码中（`_build_tools()`）
- 工具执行在 `_execute_tool()` 中直接调用 Python 代码
- 例如：`search_news` → 调用 `SearchTool.fetch()`

```python
# openai_compatible.py
async def _execute_tool(self, tool_name: str, tool_args: dict) -> dict:
    if tool_name == "search_news" and self._search_tool:
        result = await self._search_tool.fetch(...)  # 直接调用 Python 代码
        return {"success": True, "data": result.data}
```

### Kimi 的实现（如果使用 Formula 工具）

**工具执行方式**：
- 工具定义需要通过 HTTP API 获取：`GET /formulas/{uri}/tools`
- 工具执行需要通过 HTTP API 调用：`POST /formulas/{uri}/fibers`
- 结果在 `encrypted_output` 或 `output` 字段

```python
# 需要特殊处理
async def _execute_formula_tool(self, tool_name: str, tool_args: dict) -> str:
    uri = self.tool_to_formula_uri[tool_name]
    response = await self.httpx_client.post(
        f"{self.base_url}/formulas/{uri}/fibers",  # 通过 HTTP API 调用
        json={"name": tool_name, "arguments": json.dumps(tool_args)}
    )
    fiber = response.json()
    return fiber["context"].get("encrypted_output") or fiber["context"].get("output")
```

## 两种实现方案

### 方案 A：简单方案（不使用 Formula 工具）

如果**不使用** Kimi 官方工具，可以像 Qwen 一样直接使用 `OpenAICompatibleEngine`：

```python
# factory.py
if provider == "kimi":
    return OpenAICompatibleEngine(
        provider="kimi",
        api_key=MOONSHOT_API_KEY,
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k2-turbo-preview",
        enable_search=False,  # Kimi 没有这个参数
        ...
    )
```

**优点**：
- 代码量最小
- 与 Qwen 实现完全一致
- 可以使用项目自己的工具（search_news, get_price 等）

**缺点**：
- 无法使用 Kimi 官方工具（web-search, memory 等）

### 方案 B：完整方案（使用 Formula 工具）

如果需要使用 Kimi 官方工具，需要扩展 `OpenAICompatibleEngine`：

```python
class KimiDeepAnalysisEngine(OpenAICompatibleEngine):
    """支持 Formula 工具的 Kimi 引擎"""
    
    async def _load_formula_tools(self):
        # 通过 HTTP API 获取工具定义
        ...
    
    async def _execute_tool(self, tool_name: str, tool_args: dict):
        # 判断是否为 Formula 工具
        if tool_name in self.tool_to_formula_uri:
            return await self._execute_formula_tool(...)  # HTTP API 调用
        else:
            return await super()._execute_tool(...)  # 标准工具调用
```

**优点**：
- 可以使用 Kimi 官方工具（web-search 等）
- 可以同时使用项目自己的工具

**缺点**：
- 需要额外代码处理 Formula 工具
- 需要维护 `tool_name -> formula_uri` 映射

## 推荐方案

根据你的需求选择：

1. **如果只需要基础分析能力**：使用方案 A（与 Qwen 一样）
2. **如果需要使用 web-search 等官方工具**：使用方案 B（需要特殊处理）

## 代码对比

### Qwen（当前实现）

```python
# factory.py
if provider == "qwen":
    return OpenAICompatibleEngine(
        provider="qwen",
        enable_search=True,  # 特殊参数
        ...
    )
```

### Kimi 方案 A（简单，与 Qwen 类似）

```python
# factory.py
if provider == "kimi":
    return OpenAICompatibleEngine(
        provider="kimi",
        enable_search=False,  # Kimi 没有这个
        ...
    )
```

### Kimi 方案 B（完整，支持 Formula）

```python
# factory.py
if provider == "kimi":
    return KimiDeepAnalysisEngine(  # 需要新建这个类
        provider="kimi",
        formula_uris=["moonshot/web-search:latest"],
        ...
    )
```

## 总结

- **基础 API 调用**：Kimi 与 Qwen **完全一样**（都是 OpenAI 兼容）
- **工具执行**：
  - 如果不用 Formula 工具：**完全一样**（都可以用方案 A）
  - 如果用 Formula 工具：**需要特殊处理**（需要方案 B）

建议：先实现方案 A，如果需要 Formula 工具再升级到方案 B。
