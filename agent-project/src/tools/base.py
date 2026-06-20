# ============================================
# 2026-06-19 - 工具注册中心
# 职责：定义工具的格式、注册工具、管理工具列表
# 核心概念：
#   Tool = 一个 Python 函数 + 它的 JSON Schema 描述
#   DeepSeek 看到 Schema 后决定要不要调用这个函数
# ============================================

from typing import Callable, Any
from loguru import logger


class Tool:
    """
    单个工具的封装

    每个工具包含：
      - name:        函数名（DeepSeek 通过这个名字调用）
      - description: 函数说明（DeepSeek 根据这个判断何时调用）
      - parameters:  参数 JSON Schema（DeepSeek 按这个格式传参）
      - func:        实际的 Python 函数
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        func: Callable,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func

    def to_openai_format(self) -> dict:
        """
        转为 OpenAI/DeepSeek 的 function calling 格式

        OpenAI 要求的格式：
        {
          "type": "function",
          "function": {
            "name": "get_weather",
            "description": "获取某个城市的天气",
            "parameters": { ... }
          }
        }
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def execute(self, arguments: dict) -> Any:
        """
        执行工具函数

        参数:
            arguments: 模型传过来的参数 dict，例如 {"city": "北京"}

        返回:
            函数的返回值
        """
        logger.info(f"执行工具: {self.name}({arguments})")
        try:
            result = self.func(**arguments)
            logger.info(f"工具 {self.name} 返回: {str(result)[:100]}")
            return result
        except Exception as e:
            error_msg = f"工具 {self.name} 执行失败: {e}"
            logger.error(error_msg)
            return error_msg


class ToolRegistry:
    """
    工具注册中心

    管理所有可用工具：添加、查找、获取 Schema 列表

    使用方式：
        registry = ToolRegistry()
        registry.register(my_tool)           # 注册一个工具
        tools_schema = registry.get_schemas() # 获取所有工具的 Schema
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具"""
        self._tools[tool.name] = tool
        logger.info(f"工具已注册: {tool.name}")

    def get(self, name: str) -> Tool | None:
        """按名称获取工具"""
        return self._tools.get(name)

    def get_schemas(self) -> list[dict]:
        """获取所有工具的 OpenAI 格式 Schema 列表"""
        return [tool.to_openai_format() for tool in self._tools.values()]

    def list_names(self) -> list[str]:
        """列出所有已注册的工具名称"""
        return list(self._tools.keys())

    def execute(self, name: str, arguments: dict) -> Any:
        """按名称执行工具，未找到返回错误信息"""
        tool = self.get(name)
        if not tool:
            return f"错误：未找到工具 '{name}'，可用工具: {self.list_names()}"
        return tool.execute(arguments)

    @property
    def count(self) -> int:
        """已注册的工具数量"""
        return len(self._tools)