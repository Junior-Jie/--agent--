# ============================================
# 2026-06-20 - 时间技能
# ============================================

from datetime import datetime
from src.skills.base import Skill
from src.tools.base import Tool


class TimeSkill(Skill):
    """获取当前日期和时间"""

    @property
    def name(self) -> str: return "time"

    @property
    def description(self) -> str: return "获取当前日期、时间、星期"

    @property
    def prompt_hint(self) -> str: return "- 时间: 当用户问时间/日期/星期时使用 get_current_time"

    def get_tools(self) -> list[Tool]:
        def _get_time() -> str:
            now = datetime.now()
            return now.strftime("%Y年%m月%d日 %H:%M:%S 星期%w")

        return [
            Tool(
                name="get_current_time",
                description="获取当前日期和时间。当用户问时间、日期、星期时使用。",
                parameters={"type": "object", "properties": {}, "required": []},
                func=_get_time,
            )
        ]