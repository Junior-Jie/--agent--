# ============================================
# 2026-06-19 - Agent 抽象基类
# 职责：定义所有 Agent 的统一接口
#
# 为什么需要基类？
#   路由系统需要一套统一的方法来调用不同的 Agent
#   不管底层是 ChatAgent / ToolAgent / RAGAgent
#   对外都是 agent.chat("你好") agent.reset() agent.get_history()
# ============================================

from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """
    Agent 抽象基类

    所有 Agent 必须实现：
      - chat:   核心对话方法
      - reset:  重置对话历史
      - history: 获取历史（用于调试/展示）
      - name:   返回名字（用于日志/UI 显示）
      - description: 一句话说明（路由时展示）
    """

    @abstractmethod
    def chat(self, user_input: str, stream: bool = True) -> str:
        """
        发送消息，获取回复

        参数:
            user_input: 用户输入
            stream:     是否流式输出

        返回:
            模型的完整回复
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """重置对话历史，开始新一轮对话"""
        ...

    @abstractmethod
    def get_history(self) -> list:
        """获取当前对话历史"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 名称"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """一句话描述，给路由器参考"""
        ...