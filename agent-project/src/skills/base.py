# ============================================
# 2026-06-20 - 技能基类
# 职责：定义技能的抽象接口、加载/卸载机制
#
# Skill = 一组工具 + 系统提示词增强
# 每颗 Skill 是独立的能力芯片，可插拔到任意 Agent
#
# 对比 MCP：
#   MCP：外部进程暴露工具 → Agent 通过 JSON-RPC 发现调用
#   Skill：本地模块注册工具 + 提示词 → Agent 内部调用
#   两者互补，Skill 管本地能力，MCP 管远程能力
# ============================================

from abc import ABC, abstractmethod
from loguru import logger
from src.tools.base import Tool, ToolRegistry


class Skill(ABC):
    """技能基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """技能唯一名称，如 'time'、'ticket'、'weather'"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """技能描述，给路由/UI 展示"""
        ...

    @abstractmethod
    def get_tools(self) -> list[Tool]:
        """返回此技能提供的工具列表"""
        ...

    @property
    def prompt_hint(self) -> str:
        """注入到 Agent 系统提示词的说明"""
        return f"- {self.name}: {self.description}"

    @property
    def tool_count(self) -> int:
        return len(self.get_tools())


class SkillManager:
    """技能管理器：注册、卸载、注入"""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> int:
        """加载技能，返回注册的工具数"""
        self._skills[skill.name] = skill
        logger.info(f"技能已加载: {skill.name} ({skill.tool_count} 工具) — {skill.description}")
        return skill.tool_count

    def unregister(self, name: str) -> bool:
        """卸载技能"""
        if name in self._skills:
            del self._skills[name]
            logger.info(f"技能已卸载: {name}")
            return True
        return False

    def inject_to_registry(self, registry: ToolRegistry) -> int:
        """将所有技能的工具注入到 ToolRegistry"""
        total = 0
        for skill in self._skills.values():
            for tool in skill.get_tools():
                if not registry.get(tool.name):
                    registry.register(tool)
                    total += 1
        return total

    def get_prompt_context(self) -> str:
        """获取所有技能的提示词上下文"""
        lines = ["## 已加载技能"]
        for skill in self._skills.values():
            lines.append(skill.prompt_hint)
        return "\n".join(lines)

    def list(self) -> list[dict]:
        """列出所有已加载技能"""
        return [
            {"name": s.name, "description": s.description, "tools": s.tool_count}
            for s in self._skills.values()
        ]

    @property
    def count(self) -> int:
        return len(self._skills)

    @property
    def total_tools(self) -> int:
        return sum(s.tool_count for s in self._skills.values())