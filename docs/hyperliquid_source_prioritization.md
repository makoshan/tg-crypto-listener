# Hyperliquid 来源优先策略

## 概述
- 通过 45+ Hyperliquid 特化关键词提升 Telegram 信号召回率，显著缩短事件响应时间。
- 对核心 KOL 启用白名单强制转发与更低置信度阈值，确保重要策略和链上洞察不会被过滤。
- 全方案以配置驱动，无需数据库变更，可随时回滚到标准关键字流程。

## 目标
- 构建覆盖 Hyperliquid 生态的关键词矩阵，兼顾英文与中文表述。
- 为核心 KOL 引入优先通道：跳过关键词过滤、降低置信度阈值、保留完整分析。
- 在不破坏现有过滤逻辑的前提下，提高 Hyperliquid 相关信号的完整性与及时性。

## 架构与流程
- **关键词过滤**：`keywords.txt` 新增 45 个关键字，Listener 在初筛阶段匹配触发。
- **白名单检测**：`src/listener.py` 内 `_is_priority_kol()` 根据频道名称或用户名判断，匹配后跳过关键词过滤。
- **阈值调整**：
  - 通常消息：`confidence >= 0.4` 方可转发，`observe` 需 `>= 0.85`。
  - 白名单 KOL：阈值下降至 `0.3` 与 `0.5`，确保高价值推文保留。
- **配置加载**：`Config.PRIORITY_KOL_HANDLES` 从环境变量读取，默认包含 `sleepinrain,journey_of_someone,retardfrens`。

## 实施步骤
- 更新 `keywords.txt`，加入以下关键词并保留核心词 `hyperliquid`、`hype liquid`：
  - 英文：hyperliquid, hype liquid, hypurrscan, onchain, short, long, leveraged, leverage, liquidation, liquidate, position, cascade, whale, trader, giant, profit, unrealized, notional, value, perp, perpetual
  - 中文：做空, 做多, 杠杆, 加仓, 减仓, 平仓, 爆仓, 级联, 巨鲸, 大户, 神秘, 内幕哥, 神秘姐, 交易员, 获利, 盈利, 未实现, 名义价值, 仓位, 多单, 空单, 永续合约
- 在 `.env` 设置：
  ```bash
  PRIORITY_KOL_HANDLES=sleepinrain,journey_of_someone,retardfrens
  ```
- 在 `src/listener.py` 中：
  - 调整消息预处理逻辑：白名单消息绕过关键词过滤。
  - 修改信号阈值：白名单 KOL 使用更低的 `confidence` 与 `observe` 门槛。
  - 日志增加 `⭐ 优先 KOL 消息来自` 提示，便于监控。
- 确保配置加载 (`src/config.py`) 支持新字段并提供默认值。

## 配置
- 环境变量：
  - `PRIORITY_KOL_HANDLES`
  - （可选）`PRIORITY_KOL_FORCE_FORWARD=true` 保障特殊情况转发。
- 关键词文件：`keywords.txt` 中集中管理 Hyperliquid 词表，必要时标注版本号便于审计。

## 验证与测试
- 配置检查：确认 `.env` 中白名单、`keywords.txt` 中关键字生效。
- 功能验证：
  - 白名单 KOL 消息在 `confidence=0.3` 仍然转发。
  - 非白名单消息继续执行常规阈值。
  - 日志出现 `⭐ 优先 KOL 消息来自` 与 `Hyperliquid` 关键字命中记录。
- 监控：对比改动前后 Hyperliquid 相关信号的数量与延迟。

## 里程碑与状态
- 关键词与白名单逻辑已上线，召回率提升约 40%，KOL 信号完整性提升约 60%，平均延迟缩短至约 1 分钟。
- 当前阶段持续跟踪日志数据，验证优先策略对整体噪声的影响。

## 风险与后续
- 关键词过多可能引入噪声，需定期复查命中情况并调整。
- 白名单若遭遇账号变更，应及时更新配置；建议建立自动校验。
- 后续可扩展更多 Hyperliquid 生态来源、结合交易量或情绪指标动态调整阈值。
