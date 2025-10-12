"""格式化 Helper 函数"""
from typing import Any


def format_memory_evidence(entries: list[dict]) -> str:
    """
    格式化记忆证据为可读文本

    Args:
        entries: 记忆条目列表（prompt_dict 格式）

    Returns:
        str: 格式化的文本，用于 AI prompt
    """
    if not entries:
        return "无历史相似事件"

    lines = []
    for i, entry in enumerate(entries, 1):
        confidence = entry.get("confidence", "N/A")
        similarity = entry.get("similarity", "N/A")
        summary = entry.get("summary", "N/A")
        lines.append(f"{i}. {summary} (置信度: {confidence}, 相似度: {similarity})")

    return "\n".join(lines)


def format_search_evidence(search_ev: dict | None) -> str:
    """
    简要格式化搜索证据（用于 Tool Planner prompt）

    Args:
        search_ev: 搜索证据字典

    Returns:
        str: 简要描述
    """
    if not search_ev:
        return "无"

    data = search_ev.get("data", {})
    keyword = data.get("keyword", "未知关键词")
    source_count = data.get("source_count", 0)
    multi_source = data.get("multi_source", False)
    official_confirmed = data.get("official_confirmed", False)

    return (
        f"关键词: {keyword}; "
        f"结果数: {source_count}; "
        f"多源确认={multi_source}; "
        f"官方确认={official_confirmed}"
    )


def format_search_detail(search_ev: dict | None) -> str:
    """
    详细格式化搜索证据（用于 Synthesis prompt）

    Args:
        search_ev: 搜索证据字典

    Returns:
        str: 详细描述，包含前 3 条搜索结果
    """
    if not search_ev or not search_ev.get("success"):
        return "无搜索结果或搜索失败"

    data = search_ev.get("data", {})
    results = data.get("results", [])

    lines = [
        f"关键词: {data.get('keyword', 'N/A')}",
        f"结果数: {data.get('source_count', 0)}",
        f"多源确认: {data.get('multi_source', False)}",
        f"官方确认: {data.get('official_confirmed', False)}",
        f"情绪分析: {data.get('sentiment', {})}",
        "",
        "搜索结果:"
    ]

    for i, result in enumerate(results[:3], 1):  # 显示前 3 条
        title = result.get("title", "N/A")
        source = result.get("source", "N/A")
        score = result.get("score", 0.0)
        lines.append(f"{i}. {title} (来源: {source}, 评分: {score})")

    return "\n".join(lines)


def format_price_evidence(price_ev: dict | None) -> str:
    """
    详细格式化价格证据（用于 Synthesis prompt）

    Args:
        price_ev: 价格证据字典

    Returns:
        str: 详细描述，包含价格指标和异常标志
    """
    if not price_ev or not price_ev.get("success"):
        return "无价格数据或获取失败"

    data = price_ev.get("data", {})
    metrics = data.get("metrics", {})
    anomalies = data.get("anomalies", {})
    notes = data.get("notes", "")

    lines = [
        f"资产: {data.get('asset', 'N/A')}",
        f"当前价格: ${metrics.get('price_usd', 'N/A')}",
        f"偏离锚定价: {metrics.get('deviation_pct', 'N/A')}%",
        f"24h 价格变动: {metrics.get('price_change_24h_pct', 'N/A')}%",
        f"24h 成交量: ${metrics.get('volume_24h_usd', 'N/A')}",
        f"波动率 (24h): {metrics.get('volatility_24h', 'N/A')}%",
        "",
        "异常检测:",
        f"- 稳定币脱锚: {anomalies.get('price_depeg', False)}",
        f"- 波动率异常: {anomalies.get('volatility_spike', False)}",
        f"- 资金费率极端: {anomalies.get('funding_extreme', False)}",
        "",
        f"备注: {notes}"
    ]

    return "\n".join(lines)
