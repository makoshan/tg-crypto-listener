# tg-crypto-listener

一个面向加密市场的 Telegram 消息监听与智能信号转发服务。它在完成消息过滤、去重后，可选地接入翻译与 Gemini 推断，将快讯整理成结构化摘要与行动建议，并转发到你的目标频道或后续交易系统。

> 更完整的 AI 信号规划、演进路线可参考 `docs/ai_signal_plan_cn.md` 与 `docs/aisignalengine_implementation.md`。

## 核心特点
- **多源监听**：基于 Telethon 订阅多个 Telegram 频道，支持关键词过滤与消息去重。
- **AI 结构化**：调用 Google Gemini 生成 JSON 结果（摘要、事件类型、action、confidence、risk_flags 等），遇到 503/超时自动降级为纯转发。
- **可选翻译**：整合官方 `deepl` SDK，将非中文快讯先转为简体中文再送入 AI。
- **可观测性**：周期性输出运行统计，便于监控转发、AI 成功率、错误等指标。

## 环境要求
- Python 3.9 或以上（推荐 3.10+ 并使用 OpenSSL ≥ 1.1.1）。
- Telegram API ID / HASH / 手机号。
- （可选）Google Gemini API Key、DeepL API Key。

## 快速开始
1. 克隆仓库并配置 `.env`（参考 `.env` 文件中注释，填写 Telegram、Gemini、DeepL 等凭证）。
2. 使用 `uvx` 一键安装依赖并启动监听：

   ```bash
   uvx --with-requirements requirements.txt python -m src.listener
   ```

   该命令会创建临时隔离环境、同步依赖，并直接运行 Telethon 监听器。

### 手动创建虚拟环境（备选方案）
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.listener
```

### 使用 PM2 常驻（推荐生产环境）
项目附带 `ecosystem.config.js` 和 `package.json` 中的脚本，可通过 npm + PM2 管理进程。配置使用 `uvx --with-requirements requirements.txt python -m src.listener` 自动拉起依赖：

```bash
# 一次性安装依赖并启动
npm install
npm run start

# 停止 / 重启 / 查看状态 / 查看日志
npm run stop
npm run restart
npm run status
npm run logs
```

PM2 会读取 `.env`，守护进程并在崩溃后自动拉起，也可配合 `pm2 monit` 查看实时资源占用。

## 关键配置（`.env`）
| 变量 | 说明 |
| --- | --- |
| `AI_ENABLED` | 是否启用 Gemini 信号分析（`true`/`false`）。|
| `AI_MODEL_NAME` | Gemini 模型名称（默认 `gemini-2.5-flash`）。|
| `AI_TIMEOUT_SECONDS` / `AI_RETRY_ATTEMPTS` / `AI_RETRY_BACKOFF_SECONDS` | AI 调用超时与重试策略。|
| `AI_MAX_CONCURRENCY` | 同时运行的 Gemini 请求数；遇到 503 可调低。|
| `AI_SKIP_NEUTRAL_FORWARD` | 当 AI 判定为观望/低优先级时是否直接跳过转发。|
| `TRANSLATION_ENABLED` / `DEEPL_API_KEY` | 是否启用 DeepL 翻译及相应凭证。|
| `SOURCE_CHANNELS` / `TARGET_CHAT_ID` | Telegram 源频道与目标推送频道。|

修改配置后需重启服务以生效。

## 常用脚本
- `scripts/gemini_stream_example.py`：快速验证 Gemini API Key 或 Prompt，支持命令行参数、文件输入。
- `src/ai/translator.py`：DeepL 翻译封装，监听器中失败时会回退至原文。

## 故障排查
- **Gemini 503 / UNAVAILABLE**：多因 Google 服务波动或配额不足。可降低 `AI_MAX_CONCURRENCY`、调大退避、临时关闭 `AI_ENABLED`，待服务恢复后再启用。
- **LibreSSL 警告**：macOS 自带 Python 使用 LibreSSL；可安装基于 OpenSSL 的 Python，或在代码中通过 `urllib3.disable_warnings` 抑制提示。
- **DeepL 异常**：确认网络和 API Key，翻译失败时管线仍会继续处理原文。

## 运行监控
监听器每 5 分钟输出一次运行统计（已转发、过滤、AI 成功/失败次数等），可通过调整 `.env` 中的 `LOG_LEVEL` 控制日志详细程度。

## 更多资料
- AI 信号方案与演进：`docs/ai_signal_plan_cn.md`
- AI 模块实现说明：`docs/aisignalengine_implementation.md`
- PM2 部署示例：`ecosystem.config.js`

## 许可证
如仓库根目录尚未声明许可证，建议尽快补充；否则默认继承项目既有授权条款。
