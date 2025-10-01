# AiSignalEngine 实现说明

## 1. 设计目标
- 在消息过滤和去重之后接入 AI 推断，输出结构化信号。
- 兼容无 AI 场景（降级为原始转发），不阻塞 Telethon 主流程。
- 为后续接入交易执行、风控、数据沉淀提供统一接口。

## 2. 模块结构
```
src/
  ai/
    __init__.py
    gemini_client.py   # Gemini API 封装
    prompts.py         # 提示模板与格式化
    signal_engine.py   # AiSignalEngine 主类
```

## 3. 配置项
在 `Config` 增加：
- `AI_ENABLED` (bool, 默认 False)
- `GEMINI_API_KEY`
- `AI_MODEL_NAME` (默认 `gemini-2.5-flash-lite`)
- `AI_SIGNAL_THRESHOLD` (0-1)
- `AI_TIMEOUT_SECONDS`
- `AI_MAX_CONCURRENCY` (速率限制)
- `AI_RETRY_ATTEMPTS` (重试次数，默认 1 次额外尝试)
- `AI_RETRY_BACKOFF_SECONDS` (指数退避起始等待秒数)

在 `.env` 中同步新增注释示例，并在 `Config.validate` 中当 `AI_ENABLED` 为 True 时检查必填字段。

## 4. GeminiClient
- 依赖：`google-genai`、`httpx`（若使用异步 HTTP）。
- 功能：
  - 通过 `GEMINI_API_KEY` 初始化。
  - 内部创建 `genai.Client(api_key=...)` 并调用 `client.models.generate_content`。
  - 暴露 `async def generate_signal(payload: GeminiRequest) -> GeminiResponse`。
- 内部处理：请求构建、超时控制、错误重试、HTTP 429 退避。
  - 当前实现内置指数退避：超时或临时网络错误会按 `AI_RETRY_ATTEMPTS` 进行重试。
  - 示例：`uv pip install --upgrade google-genai` 后即可使用 `from google import genai`。
- 错误处理：
  - 超时/异常 → 抛出自定义 `AiServiceError`。
  - 日志记录请求上下文（脱敏后）。

## 5. 提示模板（prompts.py）
- 定义 `build_signal_prompt(event)`：
  - 输入字段：原文、译文、来源、时间、已有事件标签。
  - 输出约束：要求模型返回 JSON 字符串，包含：
    ```json
    {
      "summary": "",
      "event_type": "listing|delisting|hack|regulation|funding|whale|liquidation|partnership|product_launch|governance|macro|celebrity|airdrop|other",
      "asset": "主要涉及的币种，用大写代码，多个以逗号分隔",
      "action": "buy|sell|observe",
      "direction": "long|short|neutral",
      "confidence": 0.0,
      "strength": "low|medium|high",
      "notes": "",
      "risk_flags": []
    }
    ```
- 提供 few-shot 示例，强调必须返回可解析 JSON。

## 6. AiSignalEngine 主流程
```python
class AiSignalEngine:
    def __init__(self, client: GeminiClient, threshold: float, semaphore: asyncio.Semaphore):
        ...

    async def analyse(self, event: EventPayload) -> SignalResult:
        if not self.enabled:
            return SignalResult.skip()
        async with self.semaphore:
            prompt = build_signal_prompt(event)
            try:
                response = await self.client.generate_signal(prompt)
            except AiServiceError as err:
                logger.warning("AI 调用失败: %s", err)
                return SignalResult.error(err)
            return parse_signal_response(response, threshold=self.threshold)
```
- `EventPayload`：消息文本、译文、来源、时间、哈希。
- `SignalResult`：包含 `status` (`success|skip|error`)、解析后的结构、推荐动作、是否触发热路径。
- `parse_signal_response`：验证 JSON、字段范围；若缺失字段 → 标记错误并回退。

## 7. 与 Listener 集成
在 `TelegramListener._handle_new_message`：
1. 构造 `event_payload`。
2. `signal = await ai_engine.analyse(event_payload)`。
3. 若 `signal.should_execute_hot_path`：
   - 调用交易执行器。
   - 更新统计和日志。
4. 在转发消息中附加 AI 结果（摘要、置信度、建议动作）。
5. 将事件与 `SignalResult` 同步写入 Supabase。

## 8. 数据落地
- 新建 `src/storage/supabase_client.py`（或直接在现有数据层中扩展）。
- 存储流程：
  - `events`: 写入原始消息、AI 标签、信号详情。
  - `trades`: 由执行器产生后回写。
- 引入异步队列或批量写入，避免阻塞监听线程。

## 9. 测试策略
- 使用假客户端 `FakeGeminiClient`，返回预定义 JSON，测试 `AiSignalEngine` 正常路径、异常路径。
- 对 `parse_signal_response` 编写单元测试，覆盖字段缺失/格式错误。_
- 集成测试：模拟消息事件，断言 Listener 调用了 AI 并正确格式化输出。

## 10. 渐进式上线
1. **阶段一**：AI 仅生成摘要 & 标签，不触发交易，记录日志。
2. **阶段二**：开启热路径小仓位交易，手动监控结果。
3. **阶段三**：引入温路径确认、盘口信号、风险熔断阈值。\n
## 11. 后续优化点
- 增加缓存：同一事件哈希在短期内重复时复用结果。
- 支持多模型 A/B（Gemini + OpenAI / Claude），结合价格反馈调节权重。
- 在 TG 通知中呈现行动矩阵指标，方便人工复核。
- 结合事件-收益统计自动调节 `AI_SIGNAL_THRESHOLD`。\n
## 12. 依赖与部署注意
- 在 `requirements.txt` 添加：`google-genai`, `httpx`, `pydantic`（用于请求/响应校验），并使用 `uv pip sync requirements.txt` 快速拉取依赖。
- `.env` 新增 AI 配置项，并在 PM2 `env` 中暴露 `GEMINI_API_KEY`。
- 网络受限环境需配置代理或允许出站访问 Google API。

## 13. 实施顺序
1. 搭建 `src/ai` 目录与骨架类，保证降级逻辑可运行。\n2. 接入 Gemini Client，跑小样本验证解析稳定性。\n3. 关联 Supabase 写入与交易执行模块，完成端到端闭环。\n4. 增加监控、速率限制、日志追踪，筹备实盘灰度。\n
## 当前实现备注
- `.env` 已添加 AI 相关开关，缺失 Key 时自动降级并继续推送。
- `AiSignalEngine.from_config` 在初始化失败时记录日志，保持消息链路可用。
- `_build_ai_kwargs` 将 AI 结果插入 `format_forwarded_message`，确保最终输出统一走 TG。
- `format_forwarded_message` 负责生成紧凑通知：自动拼接翻译+摘要、按需展示操作要点，并依据 `SignalResult` 判断是否附带原文。
- `_persist_event` 方法作为 Supabase 扩展点，后续可在此写入事件与交易记录。


# 如何执行代码
uvx --with-requirements requirements.txt python -m src.listener
