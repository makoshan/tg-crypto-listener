"""LangGraph 节点的抽象基类"""
from abc import ABC, abstractmethod
from typing import Any, Mapping


class BaseNode(ABC):
    """所有 LangGraph 节点的基类，标准化接口"""

    def __init__(self, engine: "GeminiDeepAnalysisEngine") -> None:
        """
        初始化节点

        Args:
            engine: GeminiDeepAnalysisEngine 实例，提供访问客户端、配置、工具等
        """
        self.engine = engine
        # 每个节点可以访问 engine 的所有属性
        # self.engine._client - Gemini 客户端
        # self.engine._memory - 记忆仓储
        # self.engine._config - 配置对象
        # self.engine._search_tool - 搜索工具（如果已初始化）

    @abstractmethod
    async def execute(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """
        执行节点逻辑，返回状态更新字典

        Args:
            state: 当前 LangGraph 状态对象

        Returns:
            Dict: 状态更新字典，将被合并到 state 中
        """
        pass
