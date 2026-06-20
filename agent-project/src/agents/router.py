# ============================================
# 2026-06-19 - 意图路由器
# 职责：自动判断用户问题类型，分发给合适的 Agent
#
# 工作流程：
#   "忘记密码怎么办？" → 路由判断 → RAG Agent
#   "现在几点了？"     → 路由判断 → Tool Agent
#   "你好！"           → 路由判断 → Chat Agent
#
# 路由策略：
#   1. 关键词预筛（快速、免费、不上 LLM）
#   2. LLM 分类（慢但准，关键词搞不定时兜底）
# ============================================

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from loguru import logger

from src.agents.base import BaseAgent
from src.agents.chat_agent import ChatAgent
from src.agents.tool_agent import ToolAgent
from src.agents.rag_agent import RAGAgent
from src.config.settings import settings


class IntentRouter:
    """
    意图路由器

    根据用户输入自动选择最合适的 Agent

    使用方式：
        router = IntentRouter(chat_agent, tool_agent, rag_agent)
        agent, intent = router.route("忘记密码怎么办？")
        reply = agent.chat("忘记密码怎么办？")
    """

    def __init__(
        self,
        chat_agent: ChatAgent = None,
        tool_agent: ToolAgent = None,
        rag_agent: RAGAgent = None,
    ):
        """
        初始化路由器

        参数:
            chat_agent: 通用对话 Agent
            tool_agent: 工具调用 Agent
            rag_agent:  知识库 RAG Agent
        """
        self.chat_agent = chat_agent
        self.tool_agent = tool_agent
        self.rag_agent = rag_agent

        # 构建 Agent 注册表
        self._agents: dict[str, BaseAgent] = {}
        self._register_agents()

        # 对话会话追踪：每个意图独立维护对话
        self._session_agent: BaseAgent | None = None
        self._session_intent: str = "chat"

        logger.info(
            f"IntentRouter 就绪: {len(self._agents)} 个 Agent "
            f"({list(self._agents.keys())})"
        )

    def _register_agents(self) -> None:
        """注册所有可用的 Agent"""
        if self.chat_agent:
            self._agents["chat"] = self.chat_agent
        if self.tool_agent:
            self._agents["tool"] = self.tool_agent
        if self.rag_agent:
            self._agents["rag"] = self.rag_agent

    # ========================================
    # 路由决策
    # ========================================

    def route(self, user_input: str) -> tuple[BaseAgent, str, str]:
        """
        路由决策：判断意图并返回对应 Agent

        参数:
            user_input: 用户输入

        返回:
            (agent, intent_key, reasoning) 三元组
            reasoning 是人可读的判断理由
        """
        # 策略 1: 关键词快速预筛
        intent, reason = self._keyword_route(user_input)
        if intent:
            logger.info(f"路由(关键词): {intent} - {reason}")
            return self._get_agent(intent), intent, reason

        # 策略 2: LLM 分类（兜底）
        intent, reason = self._llm_route(user_input)
        logger.info(f"路由(LLM): {intent} - {reason}")
        return self._get_agent(intent), intent, reason

    def _keyword_route(self, text: str) -> tuple[str | None, str]:
        """
        关键词预筛

        规则：
          - 包含时间词/工单号/计算词 → tool
          - 包含"文档/知识库/产品/怎么/如何/什么是" → rag
          - 以上都不匹配 → 交给 LLM 判
        """
        lower = text.lower()

        # 工具类触发词
        tool_triggers = [
            "几点", "什么时间", "今天几号", "星期",
            "算", "等于", "加", "减", "乘", "除",
        ]
        for w in tool_triggers:
            if w in lower:
                return "tool", f"触发工具关键词: '{w}'"

        # 工单号精确匹配
        import re
        if re.search(r"TK\d", text.upper()):
            return "tool", f"触发工单号: 包含 TKxxx"

        # RAG 类触发词
        rag_triggers = [
            "怎么", "如何", "什么是", "是什么", "解释", "说明",
            "方法", "步骤", "流程", "操作", "设置", "配置",
            "密码", "登录", "导出", "导入", "权限", "浏览器",
            "API", "接口", "文档", "规定", "政策", "SLA",
        ]
        for w in rag_triggers:
            if w in text:
                return "rag", f"触发知识库关键词: '{w}'"

        return None, "关键词未命中，交由 LLM 判断"

    def _llm_route(self, text: str) -> tuple[str, str]:
        """
        LLM 意图分类（轻量调用，不用完整对话）
        """
        # 构建分类提示词
        agents_desc = "\n".join([
            f"- {key}: {agent.description}"
            for key, agent in self._agents.items()
        ])

        classify_prompt = [
            {"role": "system", "content": (
                "你是一个意图分类器。根据用户输入，判断应该交给哪个 Agent 处理。\n"
                f"可用 Agent:\n{agents_desc}\n\n"
                "只返回 JSON: {\"intent\": \"chat|tool|rag\", \"reason\": \"一句话理由\"}"
            )},
            {"role": "user", "content": text},
        ]

        try:
            # 用 ChatAgent 的 client 做一次轻量调用
            if self.chat_agent:
                client = self.chat_agent.client
                response = client.chat.completions.create(
                    model=settings.DEEPSEEK_MODEL,
                    messages=classify_prompt,
                    stream=False,
                    max_tokens=100,
                    temperature=0.1,  # 低温度，稳定输出
                )
                raw = response.choices[0].message.content or ""
            else:
                return "chat", "无 LLM Client，回退到 chat"

            # 解析 JSON
            # 截取 JSON 部分（模型可能会加额外文字）
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            intent = result.get("intent", "chat")
            reason = result.get("reason", "LLM 未说明理由")
            return intent, reason

        except Exception as e:
            logger.warning(f"LLM 路由失败: {e}，回退到 chat")
            return "chat", f"LLM 路由异常({e})，回退"

    def _get_agent(self, intent: str) -> BaseAgent:
        """按意图获取 Agent，未注册的回退到 chat"""
        return self._agents.get(intent, self.chat_agent)

    # ========================================
    # 统一对话接口
    # ========================================

    def chat(self, user_input: str, stream: bool = True) -> str:
        """
        统一对话入口：自动路由 → 执行 → 返回

        参数:
            user_input: 用户输入
            stream:     流式输出

        返回:
            模型回复
        """
        agent, intent, reason = self.route(user_input)

        if stream:
            print(f"\n[路由] → {agent.name} ({reason})")

        reply = agent.chat(user_input, stream=stream)

        self._session_agent = agent
        self._session_intent = intent

        return reply

    def reset_all(self):
        """重置所有 Agent 的对话历史"""
        for agent in self._agents.values():
            agent.reset()
        self._session_agent = None
        logger.info("所有 Agent 已重置")

    def get_agents_info(self) -> list[dict]:
        """获取所有已注册 Agent 的信息"""
        return [
            {"key": k, "name": a.name, "description": a.description}
            for k, a in self._agents.items()
        ]

    @property
    def last_agent(self) -> BaseAgent | None:
        """上一次对话使用的 Agent"""
        return self._session_agent

    @property
    def last_intent(self) -> str:
        """上一次对话的意图"""
        return self._session_intent


# ============================================
# 交互式控制台
# ============================================
if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if not settings.DEEPSEEK_API_KEY or "your-deepseek-api-key" in settings.DEEPSEEK_API_KEY:
        print("[X] 请先在 .env 中配置 DEEPSEEK_API_KEY！")
        sys.exit(1)

    from src.rag.vector_store import VectorStore
    from src.rag.document_loader import DocumentLoader

    # 初始化三个 Agent
    print("[*] 初始化 Agent...")

    chat = ChatAgent(system_prompt="你是一个友好的对话助手，用中文简洁回答。")

    tool = ToolAgent()

    # RAG Agent 需要知识库
    persist_dir = os.path.join(settings.PROJECT_ROOT, "data", "chroma_db")
    vs = VectorStore(persist_dir=persist_dir)
    if vs.doc_count == 0:
        print("[!] 导入知识库文档...")
        docs_dir = os.path.join(settings.PROJECT_ROOT, "data", "documents")
        loader = DocumentLoader(chunk_size=400, chunk_overlap=50)
        chunks = loader.load_directory(docs_dir)
        vs.add_documents(chunks)
    rag = RAGAgent(vector_store=vs)

    router = IntentRouter(chat_agent=chat, tool_agent=tool, rag_agent=rag)

    print("=" * 50)
    print("  智能路由 Agent 系统")
    print(f"  已注册: {router.get_agents_info()}")
    print("  输入 quit 退出, reset 重置, /agents 查看信息")
    print("=" * 50)

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
            router.reset_all()
            print("[OK] 所有 Agent 已重置")
            continue

        if user_input == "/agents":
            for info in router.get_agents_info():
                print(f"  [{info['key']}] {info['name']}: {info['description']}")
            continue

        if not user_input:
            continue

        router.chat(user_input)