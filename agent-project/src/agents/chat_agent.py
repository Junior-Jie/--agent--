# ============================================
# 2026-06-19 - LLM 对话 Agent
# 职责：封装 DeepSeek V4 Pro 的对话能力
# 功能：多轮对话、流式输出、消息历史管理
# ============================================

import sys
import os

# 把项目根目录加入 Python 搜索路径，确保能找到 src 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from openai import OpenAI
from loguru import logger
from src.config.settings import settings
from src.agents.base import BaseAgent
from src.skills.base import SkillManager, Skill


class ChatAgent(BaseAgent):
    """
    LLM 对话 Agent

    工作流程：
        用户输入 → 拼入消息历史 → 调用 DeepSeek API → 返回回复 → 存入历史

    多轮对话原理：
        把之前的对话都发给 API，模型根据上下文给出合理回复
    """

    def __init__(self, system_prompt: str = "你是一个智能助手，请用中文回答用户问题。"):
        """
        初始化 Agent

        参数:
            system_prompt: 系统提示词，定义模型的人设和行为规则
                          可以换成任何角色（客服、翻译、写代码等）
        """
        # 1. 创建 OpenAI 客户端（指向 DeepSeek 服务器）
        self.client = OpenAI(
            base_url=settings.DEEPSEEK_BASE_URL,  # https://api.deepseek.com/v1
            api_key=settings.DEEPSEEK_API_KEY,
        )
        self.model = settings.DEEPSEEK_MODEL  # deepseek-v4-pro

        # 2. 技能管理器（可插拔能力模块）
        self.skills = SkillManager()

        # 3. 消息历史列表
        # [
        #   {"role": "system", "content": "你是一个助手"},  ← 系统人设，放在最前面
        #   {"role": "user",   "content": "你好"},         ← 用户说的话
        #   {"role": "assistant", "content": "你好！"},    ← 模型的回复
        #   ...
        # ]
        # 新对话自动追加到末尾，这样模型能看到完整上下文
        self.messages = [
            {"role": "system", "content": system_prompt}
        ]

        logger.info(f"ChatAgent 初始化完成，模型: {self.model}")

    def chat(self, user_input: str, stream: bool = True) -> str:
        """
        发送消息给模型，获取回复

        参数:
            user_input: 用户输入的文字
            stream:     True=逐字输出（打字机效果）, False=等全部生成完再返回

        返回:
            模型的完整回复文字
        """
        # 1. 把用户消息追加到历史
        self.messages.append({"role": "user", "content": user_input})

        logger.info(f"发送消息: {user_input[:50]}...")  # 只打前 50 个字符的日志

        # 2. 调用 DeepSeek API
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                stream=stream,          # 流式输出
                temperature=0.7,        # 随机性（0=死板, 1=创意），客服场景建议 0.5-0.7
                max_tokens=2048,        # 限制回复最长 2048 个 token（约 1500 个汉字）
            )

            if stream:
                # 流式模式：逐 token 输出，边生成边打印（打字机效果）
                reply = self._handle_stream(response)
            else:
                # 非流式模式：一次性获取全部回复
                reply = response.choices[0].message.content

        except Exception as e:
            logger.error(f"API 调用失败: {e}")
            reply = f"[错误] 模型调用失败: {str(e)}"

        # 3. 把模型回复追加到历史
        self.messages.append({"role": "assistant", "content": reply})

        return reply

    def _handle_stream(self, response) -> str:
        """
        处理流式响应：逐 token 打印，最后返回完整内容

        流式 vs 非流式：
            - 非流式：等 5 秒 → 一口气显示全部文字
            - 流式：0.2 秒后开始，一个字一个字往外蹦（体验更好）
        """
        full_reply = ""

        print("\n" + "=" * 50)
        print("【DeepSeek 回复】")
        print("=" * 50)

        for chunk in response:
            # 每个 chunk 包含一个 token（约 1-2 个汉字或英文单词）
            if chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                print(token, end="", flush=True)  # flush=True 确保立即显示
                full_reply += token

        print("\n" + "=" * 50 + "\n")

        return full_reply

    def reset(self):
        """
        重置对话历史，开始新一轮对话
        保留系统提示词，清空所有用户/助手消息
        """
        system_msg = self.messages[0]  # 保留系统提示词
        self.messages = [system_msg]
        logger.info("对话历史已重置")

    def get_history(self) -> list:
        """
        获取当前对话历史（方便调试或存储到数据库）

        返回:
            消息列表，每条消息包含 role 和 content
        """
        return self.messages

    @property
    def history_count(self) -> int:
        """
        当前对话轮数（不含系统提示词）

        返回:
            (消息总数 - 1) // 2，因为每轮对话 = 1 条用户消息 + 1 条助手消息
        """
        return (len(self.messages) - 1) // 2

    # ---- 技能管理 ----

    def register_skill(self, skill: Skill) -> int:
        """加载一个技能到本 Agent"""
        return self.skills.register(skill)

    def unregister_skill(self, name: str) -> bool:
        """卸载一个技能"""
        return self.skills.unregister(name)

    def list_skills(self) -> list[dict]:
        """列出已加载的技能"""
        return self.skills.list()

    # ---- 技能管理 ----

    def register_skill(self, skill: Skill) -> int:
        return self.skills.register(skill)

    def unregister_skill(self, name: str) -> bool:
        return self.skills.unregister(name)

    def list_skills(self) -> list[dict]:
        return self.skills.list()

    @property
    def name(self) -> str:
        return "对话助手"

    @property
    def description(self) -> str:
        return "通用对话 Agent，适合闲聊、简单问答、不需要工具和知识库的对话"


# ============================================
# 模块自测：当直接运行这个文件时，执行下面的测试代码
# 用法: python src/agents/chat_agent.py
# ============================================
if __name__ == "__main__":
    # 修复 Windows 终端中文/emoji 乱码问题
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # 检查 API Key 是否已配置
    if not settings.DEEPSEEK_API_KEY or "your-deepseek-api-key" in settings.DEEPSEEK_API_KEY:
        print("=" * 50)
        print("[X] 请先在 .env 文件中配置 DEEPSEEK_API_KEY！")
        print("  1. 打开 .env.example")
        print("  2. 另存为 .env")
        print("  3. 填入你的真实 DeepSeek Key")
        print("=" * 50)
        sys.exit(1)

    # 创建 Agent 实例
    agent = ChatAgent(
        system_prompt="你是一个智能助手，请用中文回答用户问题。说话简洁明了。"
    )

    # 测试：连续对话
    print("\n--- 测试 1: 单轮对话 ---")
    reply1 = agent.chat("你好，请问你是谁？")
    print(f"统计: 回复共 {len(reply1)} 字\n")

    print("--- 测试 2: 多轮对话（上下文记忆）---")
    reply2 = agent.chat("我刚才问了你什么？")
    # 如果模型正确回答了"你问我是谁"，说明多轮对话生效
    print(f"\n当前对话轮数: {agent.history_count}")

    print("\n--- 测试 3: 重置对话 ---")
    agent.reset()
    reply3 = agent.chat("我刚才问了你什么？")
    # 重置后模型应该"忘掉"之前的内容
    print(f"\n当前对话轮数: {agent.history_count}")