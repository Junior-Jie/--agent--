# ============================================
# 2026-06-19 - Tool Calling Agent
# 职责：继承 ChatAgent，新增工具调用能力
# 核心流程：
#   用户输入 → 模型判断是否需要工具
#     → 需要：执行工具 → 把结果发回模型 → 模型总结回答
#     → 不需要：直接回答（和 ChatAgent 一样）
#
# 注意：由于流式模式下 tool_calls 拼接复杂，工具调用统一用非流式
# ============================================

import json
import sys
import io
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from loguru import logger
from openai.types.chat import ChatCompletionMessage
from src.agents.chat_agent import ChatAgent
from src.tools.base import ToolRegistry
from src.tools.builtin_tools import create_tool_registry
from src.config.settings import settings


class ToolAgent(ChatAgent):
    """
    带工具调用能力的 Agent

    继承 ChatAgent（保持对话能力），新增：
      1. 携带工具 Schema 调用 API
      2. 解析模型返回的 tool_calls
      3. 执行工具并回传结果
      4. 支持多轮工具调用（一次对话可调用多次工具）
    """

    def __init__(self, system_prompt: str = None, tool_registry: ToolRegistry = None):
        if system_prompt is None:
            system_prompt = (
                "你是一个智能客服助手。请用中文回答用户问题。\n"
                "你可以使用提供的工具来查询信息：\n"
                "- 时间和日期相关的问题用 get_current_time\n"
                "- 计算问题用 calculate\n"
                "- 工单查询用 get_ticket_status\n"
                "- 产品使用问题用 search_faq\n"
                "如果工具返回了结果，请用自然语言整理后告诉用户。"
            )

        super().__init__(system_prompt=system_prompt)

        self.tool_registry = tool_registry or create_tool_registry()
        logger.info(f"ToolAgent 已加载 {self.tool_registry.count} 个工具: {self.tool_registry.list_names()}")

    @property
    def name(self) -> str:
        return "客服工具 Agent"

    @property
    def description(self) -> str:
        return "可调用工具（时间、计算、工单、FAQ），适合需要查询实时信息的请求"

    def chat(self, user_input: str, stream: bool = True) -> str:
        """
        对话入口：覆盖父类方法，增加工具调用循环

        处理流程：
          Step A: 发送用户消息 + 工具列表 → 模型决定要不要调工具
          Step B: 有工具调用 → 执行工具 → 把结果发回模型 → 回到 Step A
          Step C: 无工具调用 → 输出答案（结束）

        循环处理：一次用户输入可能触发多轮工具调用（最多 5 轮防死循环）
        """
        # 1. 把用户消息加入历史
        self.messages.append({"role": "user", "content": user_input})
        logger.info(f"发送消息: {user_input[:50]}...")

        # 2. 工具调用循环
        max_rounds = 5
        for round_num in range(max_rounds):
            # Step A: 调用 API
            # 注意：tool_calling 场景强制用非流式
            # 原因：流式模式下 tool_calls 的分片拼接在不同 SDK 版本中行为不一致
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tool_registry.get_schemas(),
                tool_choice="auto",
                stream=False,           # ← 非流式，确保 tool_calls 完整
                temperature=0.7,
                max_tokens=2048,
            )

            msg: ChatCompletionMessage = response.choices[0].message

            # Step B: 检查是否有 tool_calls
            if not msg.tool_calls:
                # Step C: 无工具调用 → 直接回答 → 结束
                reply = msg.content or ""

                # 如果有流式需求，打印出来
                if stream:
                    print("\n" + "=" * 50)
                    print("【DeepSeek 回复】")
                    print("=" * 50)
                    print(reply)
                    print("=" * 50 + "\n")

                self.messages.append({"role": "assistant", "content": reply})
                return reply

            # Step D: 有工具调用 → 逐个执行
            print("\n" + "-" * 40)
            tool_results = []
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments)

                print(f"[Tool] 调用: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")

                result = self.tool_registry.execute(tool_name, tool_args)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })

                print(f"[Tool] 返回: {str(result)[:200]}")

            print("-" * 40)

            # 把模型消息(含 tool_calls)加入历史
            self.messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in msg.tool_calls
                ]
            })

            # 把工具执行结果加入历史
            self.messages.extend(tool_results)

        # 超过最大轮数
        logger.warning(f"达到最大工具调用轮数 {max_rounds}")
        fallback = self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            stream=False,
            max_tokens=512,
        )
        reply = fallback.choices[0].message.content or "处理超时，请简化问题。"
        self.messages.append({"role": "assistant", "content": reply})
        return reply


# ============================================
# 交互式控制台
# ============================================
if __name__ == "__main__":
    if not settings.DEEPSEEK_API_KEY or "your-deepseek-api-key" in settings.DEEPSEEK_API_KEY:
        print("[X] 请先在 .env 文件中配置 DEEPSEEK_API_KEY！")
        sys.exit(1)

    agent = ToolAgent()
    print("=" * 50)
    print("  智能客服 Agent - Tool Calling")
    print("  可用工具:", agent.tool_registry.list_names())
    print("  输入 quit/exit 退出, reset 重置")
    print("=" * 50)

    # 交互循环
    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("再见！")
            break

        if user_input.lower() == "reset":
            agent.reset()
            print("[OK] 对话已重置")
            continue

        if not user_input:
            continue

        agent.chat(user_input)