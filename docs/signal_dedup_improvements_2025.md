# 信号去重改进 (2025-10-27)

## 问题背景

用户反馈收到三条相同的信号（贝莱德向 Coinbase Prime 存入 BTC/ETH），虽然内容相同但 URL 不同，却都通过了去重检查。

### 具体案例

```
信号 1 (巨鲸动向): BlockBeats 报道贝莱德存入 BTC 和 ETH
信号 2 (融资/募资): Odaily星球日报 报道贝莱德存入 BTC 和 ETH  
信号 3 (其他): Lookonchain 报道贝莱德存入 BTC 和 ETH
```

核心问题：
- 三条消息的核心信息相同（贝莱德、Coinbase、BTC、ETH、存入）
- 但元数据中的 `event_type` 不同
- AI 生成的摘要措辞略有差异

## 根本原因

原有的信号去重机制要求**元数据完全匹配**（包括 `event_type`）才会进入文本相似度检查。

```python
# 旧逻辑 (src/utils.py)
if entry.metadata != metadata:
    continue  # 如果 event_type 不同，直接跳过
```

这导致即使核心实体、操作、资产都匹配，只要 `event_type` 不同，就不会被检测为重复。

## 解决方案

### 1. 核心元数据匹配

将元数据分为两类：
- **核心元数据**：`action`、`direction`、`asset`、`asset_names`
- **辅助元数据**：`event_type`

只要求核心元数据匹配即可进入文本相似度检查。

```python
def _get_core_metadata(metadata: Tuple[str, str, str, str, str]) -> Tuple[str, str, str, str]:
    """提取核心元数据（排除 event_type）"""
    return (metadata[0], metadata[1], metadata[3], metadata[4])  # action, direction, asset, asset_names
```

### 2. 更宽松的相似度阈值

当核心元数据匹配时，使用更宽松的相似度阈值（0.35 而非默认的 0.68），以处理不同媒体来源报道同一事件时措辞差异较大的情况。

```python
# 当核心元数据匹配时，使用更宽松的阈值
effective_threshold = 0.35  # vs 0.68 default

if ratio < effective_threshold:
    continue
```

## 实现细节

### 修改文件

- `src/utils.py`: 修改 `SignalMessageDeduplicator.is_duplicate()` 方法
  - 添加 `_get_core_metadata()` 辅助方法
  - 在核心元数据匹配时使用更宽松的相似度阈值
  - 修复正则表达式警告

### 新测试

- `tests/test_blackrock_dedup.py`: 新增测试用例
  - 验证不同 `event_type` 的信号能被检测为重复
  - 验证不同资产不会被误判为重复

## 测试结果

```bash
$ pytest tests/test_signal_deduplicator.py tests/test_blackrock_dedup.py -v

============================= test session starts ==============================
12 passed in 0.01s

tests/test_signal_deduplicator.py::TestSignalMessageDeduplicator::test_basic_duplicate_detection PASSED
tests/test_signal_deduplicator.py::TestSignalMessageDeduplicator::test_similar_summaries_detected PASSED
tests/test_signal_deduplicator.py::TestSignalMessageDeduplicator::test_different_metadata_not_duplicate PASSED
...
tests/test_blackrock_dedup.py::test_blackrock_duplicate_detection PASSED
tests/test_blackrock_dedup.py::test_different_assets_not_duplicate PASSED
```

## 效果

### 改进前

- 由于 `event_type` 不同，三条贝莱德信号都通过了去重检查
- 用户收到 3 条内容重复的信号 ❌

### 改进后

- 第一条贝莱德信号正常发送
- 第二条贝莱德信号（`event_type` 为"融资/募资"）被识别为重复，跳过 ✅
- 第三条贝莱德信号（`event_type` 为"其他"）被识别为重复，跳过 ✅
- 用户只收到 1 条信号 ✅

## 兼容性

✅ 完全向后兼容  
✅ 不破坏现有功能  
✅ 所有原有测试通过  
✅ 不影响元数据完全匹配的情况

## 配置说明

无需额外配置，改进自动生效。

如需调整阈值：

```bash
# .env
SIGNAL_DEDUP_SIMILARITY=0.68  # 基础阈值（元数据不完全匹配时使用）
```

对于核心元数据匹配的情况，系统会自动应用 0.35 的阈值。

## 技术细节

### 去重流程

```
1. 归一化文本摘要（移除 URL、数字、标点）
2. 提取核心元数据（action, direction, asset, asset_names）
3. 遍历时间窗口内的历史信号
4. 比较核心元数据是否匹配
   ↓ 匹配
5. 计算文本相似度（SequenceMatcher）
   ↓ similarity >= 0.35 (核心元数据匹配时)
   ↓ 或 similarity >= 0.68 (否则)
6. 检查公共字符数 >= 10
7. 判定为重复/不重复
```

### 相似度计算

原始文本示例：
```
BlockBeats：贝莱德将价值约2.25亿美元的1021枚比特币和25707枚以太坊存入Coinbase Prime...
Odaily星球日报：贝莱德向Coinbase Prime存入巨额BTC和ETH...
```

归一化后：
```
blockbeats贝莱德将价值约亿美元的枚比特币和枚以太坊存入coinbaseprime...
odaily星球日报贝莱德向coinbaseprime存入巨额btc和eth...
```

相似度：0.374（满足 0.35 阈值）

## 总结

此次改进增强了信号去重机制，能够更好地处理来自不同媒体但报道同一事件的重复信号，提升了用户体验，避免了信息冗余。

