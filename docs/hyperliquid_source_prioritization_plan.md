# Hyperliquid Source Prioritization — Minimal Plan

## 核心策略
通过**关键词驱动 + 白名单**实现 Hyperliquid 信号精准捕获。

---

## 1. 全局关键词策略

### 1.1 添加 Hyperliquid 专业术语（30+ 关键词）

**英文**：hyperliquid, hype, hypurrscan, onchain, short, long, leveraged, leverage, liquidation, liquidate, position, cascade, whale, trader, giant, profit, unrealized, notional, value, perp

**中文**：做空, 做多, 杠杆, 加仓, 减仓, 平仓, 清算, 爆仓, 级联, 巨鲸, 大户, 神秘, 内幕哥, 神秘姐, 交易员, 获利, 盈利, 未实现, 名义价值, 仓位, 多单, 空单

### 1.2 配置位置
- 文件：`keywords.txt` 或环境变量 `FILTER_KEYWORDS`
- 作用：任意来源的消息包含这些关键词都会触发处理

⚠️ **重要**：保留 `hyperliquid` 和 `hype liquid` 关键词，不要删除

---

## 2. 白名单来源（优先处理）

### 2.1 核心 KOL 白名单

| 频道 | 专业领域 | 特殊处理 |
|-----|---------|---------|
| **@SleepinRain** | 市场分析和交易策略 | • 跳过关键词过滤<br>• confidence ≥ 0.3 保留转发<br>• 完整分析输出 |
| **@journey_of_someone** | DeFi 和链上分析 | 同上 |
| **@RetardFrens** | 社区信号和热点 | 同上 |

### 2.2 配置
```
PRIORITY_KOL_HANDLES=sleepinrain,journey_of_someone,retardfrens
```

---

## 3. 预期效果

### 信号质量提升
- Hyperliquid 召回率：+40%
- KOL 信号完整性：+60%

### 响应速度
- Hyperliquid 信号延迟：3 分钟 → 1 分钟

---

## 4. 实施检查清单

### 配置准备
- [ ] `keywords.txt` 添加 30+ Hyperliquid 关键词
- [ ] `.env` 配置 `PRIORITY_KOL_HANDLES=sleepinrain,journey_of_someone,retardfrens`

### 验证测试
- [ ] KOL 白名单消息正常转发（confidence ≥ 0.3）

---

## 附录：历史版本参考
- **cd62b50**：记忆优先策略版本（含代码示例）
- **4fafdb9**：关键词驱动架构版本（当前）
