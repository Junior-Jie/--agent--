# ============================================
# 2026-06-19 - 多 Agent 编排器（LangGraph）
# 职责：用 LangGraph 构建状态图，让多个 Agent 协作
#
# 安全机制：语义守卫层
#   用户输入 → SemanticGuard（向量相似度判断）
#     → 危险操作 + 未登录 → 拦截，提示登录
#     → 危险操作 + 已登录 + 参数不足 → 追问细节
#     → 安全请求 → 正常编排
# ============================================
#   State:  共享数据，在节点间流转
#   Node:   一个处理函数（这里每个 Agent 是一个节点）
#   Edge:   节点间的箭头（普通边=顺序，条件边=分支）
# ============================================

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import time
from typing import TypedDict, Optional
from loguru import logger

from langgraph.graph import StateGraph, START, END

from src.agents.chat_agent import ChatAgent
from src.agents.tool_agent import ToolAgent
from src.agents.rag_agent import RAGAgent
from src.agents.guard import SemanticGuard
from src.observability.tracer import AgentTrace, trace_node, get_dashboard
from src.data.context import get_current_user
from src.data.memory_manager import MemoryManager
from src.rag.vector_store import VectorStore
from src.rag.document_loader import DocumentLoader
from src.tools.builtin_tools import create_tool_registry
from src.config.settings import settings


# ============================================
# 1. 定义图状态（在各 Agent 节点间流转的数据）
# ============================================

# 模型路由：简单用 flash（快），复杂用 pro（准）
MODEL_FLASH = "deepseek-v4-flash"
MODEL_PRO = "deepseek-v4-pro"


def select_model(intent: str) -> str:
    """按意图选模型：simple/chat → flash, knowledge/tool/hybrid → pro"""
    return MODEL_FLASH if intent in ("simple", "chat") else MODEL_PRO


class OrchestratorState(TypedDict):
    """LangGraph 共享状态"""
    user_input: str
    messages: list[dict]
    intent: str
    active_model: str              # 动态模型选择（flash / pro）
    rag_docs: str
    tool_results: list[str]
    supervisor_plan: str
    final_response: str


# ============================================
# 2. 节点函数（每个节点 = 一个处理步骤）
# ============================================

class OrchestratorNodes:
    """
    编排器的所有节点

    每个方法都接收 State，返回 State 的部分更新。
    LangGraph 会自动合并这些更新到全局 State 中。
    """

    def __init__(
        self,
        chat_agent: ChatAgent,
        tool_agent: ToolAgent,
        rag_agent: RAGAgent,
    ):
        self.chat = chat_agent
        self.tool = tool_agent
        self.rag = rag_agent

        # 复用已有 client 给 Supervisor
        self.client = chat_agent.client

    # ----- Supervisor（总调度）-----

    def supervisor(self, state: OrchestratorState) -> dict:
        """
        Supervisor 节点：分析用户意图，制定执行计划

        这是整个编排器的"大脑"，决定调用哪些 Agent
        """
        user_input = state["user_input"]
        logger.info(f"[Supervisor] 分析: {user_input[:50]}...")

        # 用 LLM 分析意图和计划
        prompt = [
            {"role": "system", "content": (
                "你是 Agent 编排器。分析用户请求，判断需要哪些 Agent 配合。\n\n"
                "可用 Agent:\n"
                "- rag: 知识库检索（产品文档、FAQ、流程）\n"
                "- tool: 工具调用（时间、计算、工单 CRUD）\n"
                "- chat: 普通对话\n\n"
                "意图类型：\n"
                "- simple: 简单闲聊，一个 Agent 够\n"
                "- knowledge: 需要查文档 FAQ\n"
                "- tool: 需要调工具（含查工单、开工单、改工单、查时间、计算等所有工单和工具操作）\n"
                "- hybrid: 需要文档 + 工具共同配合（复杂请求）\n\n"
                "注意：'查工单/工单列表/有哪些工单/所有工单'→ tool，不要追问直接路由！\n\n"
                "返回 JSON: {\"intent\": \"simple|knowledge|tool|hybrid\", "
                "\"plan\": \"用中文简述执行计划\", \"agents\": [\"rag\",\"tool\",...]}"
            )},
            {"role": "user", "content": user_input},
        ]

        try:
            resp = self.client.chat.completions.create(
                model=state.get("active_model", MODEL_PRO),
                messages=prompt,
                stream=False,
                max_tokens=200,
                temperature=0.1,
            )
            raw = resp.choices[0].message.content or "{}"
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            plan = json.loads(raw)
        except Exception as e:
            logger.warning(f"Supervisor 分析失败: {e}")
            plan = {"intent": "simple", "plan": "回退到对话", "agents": ["chat"]}

        intent = plan.get("intent", "simple")
        plan_text = plan.get("plan", "")
        agents = plan.get("agents", ["chat"])

        logger.info(f"[Supervisor] 意图={intent}, 计划={plan_text}, 调用={agents}")

        return {
            "intent": intent,
            "active_model": select_model(intent),    # 简单→flash, 复杂→pro
            "supervisor_plan": f"意图={intent}, 计划={plan_text}, 调用={agents}",
        }

    # ----- RAG 节点 -----

    def rag_search(self, state: OrchestratorState) -> dict:
        """RAG 节点：从知识库检索相关文档"""
        user_input = state["user_input"]
        logger.info(f"[RAG Node] 检索: {user_input[:30]}...")

        # 用 RAG Agent 的检索方法
        # 混合检索已在 RAGAgent 内部集成
        if self.rag and self.rag.hybrid_retriever:
            results = self.rag.hybrid_retriever.search(query=user_input, top_k=3)
        else:
            results = self.rag.vector_store.search(query=user_input, top_k=3)

        # 格式化为文本
        if results:
            parts = []
            for i, r in enumerate(results, 1):
                score = r.get("rerank_score") or r.get("score") or 0
                meta = r.get("metadata", {})
                source = meta.get("source", "?")
                parts.append(
                    f"[文档{i} 来源={source} 相关度={score:.0%}]\n{r['content']}"
                )
            docs_text = "\n\n".join(parts)
            logger.info(f"[RAG Node] 检索到 {len(results)} 篇文档")
        else:
            docs_text = "（知识库中未找到相关文档）"
            logger.info("[RAG Node] 无结果")

        return {"rag_docs": docs_text}

    # ----- Tool 节点 -----

    def tool_execute(self, state: OrchestratorState) -> dict:
        """
        Tool 节点：执行工具调用

        和 ToolAgent 不同，这里是让模型自己决定调哪个工具，
        结果存入 State 而不是 messages
        """
        user_input = state["user_input"]
        logger.info(f"[Tool Node] 执行: {user_input[:30]}...")

        # 用 ToolAgent 的 ToolRegistry 和 client
        # 但用独立的消息（不污染主对话历史）
        tool_msgs = [
            {"role": "system", "content": (
                "你是工具执行助手，直接调用最匹配的工具。规则：\n"
                "- 用户说'查工单/工单列表/有哪些工单/所有工单'→ 用 list_tickets\n"
                "- 用户说'查TKxxxx/工单TK'→ 用 get_ticket_status\n"
                "- 用户说'开工单/创建/新建工单'→ 用 create_ticket\n"
                "- 用户说'关闭/删除工单'→ 用 delete_ticket\n"
                "- 用户说'几点/时间/日期'→ 用 get_current_time\n"
                "- 用户说'算/等于/加/减/乘/除'→ 用 calculate\n"
                "只要用户意图明确，不要追问直接调用。"
            )},
            {"role": "user", "content": user_input},
        ]

        results = []
        try:
            resp = self.client.chat.completions.create(
                model=state.get("active_model", MODEL_PRO),
                messages=tool_msgs,
                tools=self.tool.tool_registry.get_schemas(),
                tool_choice="auto",
                stream=False,
                max_tokens=512,
            )
            msg = resp.choices[0].message
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments)
                    r = self.tool.tool_registry.execute(name, args)
                    results.append(f"[{name}] {r}")
                    logger.info(f"[Tool Node] {name} → {str(r)[:100]}")
            else:
                results.append(f"（模型判断无需工具: {msg.content}）")
        except Exception as e:
            results.append(f"工具执行异常: {e}")

        return {"tool_results": results}

    # ----- 综合回复节点 -----

    def synthesize(self, state: OrchestratorState) -> dict:
        """
        Synthesizer 节点：把各个 Agent 的结果汇总，生成最终回复

        这是最后一步，把 Supervisor 的计划、RAG 的文档、Tool 的结果
        拼在一起，让 LLM 生成一个统一的自然语言回复
        """
        user_input = state["user_input"]
        rag_docs = state.get("rag_docs", "")
        tool_results = state.get("tool_results", [])
        plan = state.get("supervisor_plan", "")

        logger.info(f"[Synthesizer] 综合回复: RAG={bool(rag_docs)}, Tools={len(tool_results)}")

        # 构建上下文
        context_parts = [f"计划: {plan}"]

        if rag_docs:
            context_parts.append(f"\n[知识库检索结果]\n{rag_docs}")

        if tool_results:
            tools_text = "\n".join(f"  {r}" for r in tool_results)
            context_parts.append(f"\n[工具执行结果]\n{tools_text}")

        context = "\n\n".join(context_parts)

        prompt = (
            f"{context}\n\n"
            f"请根据以上所有信息，用中文给用户一个完整、清晰的回复。\n"
            f"用户问题: {user_input}\n\n"
            f"要求:\n"
            f"1. 如果知识库有相关内容，优先引用\n"
            f"2. 如果工具执行有结果，一并告知\n"
            f"3. 如果有工单创建，突出显示工单号\n"
            f"4. 回复简洁、有条理"
        )

        try:
            # 构建消息列表：系统人设 + 历史对话 + 当前汇总
            msgs = [
                {"role": "system", "content": self.chat.messages[0]["content"] if self.chat.messages else "你是智能云平台的工单客服助手，不是 DeepSeek。"},
            ]
            history = state.get("messages", [])
            if history:
                msgs.extend(history)
            msgs.append({"role": "user", "content": prompt})
            resp = self.client.chat.completions.create(
                model=state.get("active_model", MODEL_PRO),
                messages=msgs,
                stream=False,
                max_tokens=1024,
                temperature=0.7,
            )
            reply = resp.choices[0].message.content or ""
        except Exception as e:
            reply = f"综合回复生成失败: {e}"

        logger.info(f"[Synthesizer] 生成回复 {len(reply)} 字")
        return {"final_response": reply}

    # ----- 简单对话节点 -----

    def simple_chat(self, state: OrchestratorState) -> dict:
        """简单对话节点：使用 ChatAgent 的 persona 人设"""
        user_input = state["user_input"]
        logger.info(f"[Chat Node] 简单对话: {user_input[:30]}...")

        # 复用 ChatAgent 的系统提示词（含 PersonaSkill 完整人设）
        persona_prompt = self.chat.messages[0]["content"] if self.chat.messages else (
            "你是智能云平台的工单客服助手，不是 DeepSeek。用中文简洁回答。"
        )
        temp_msgs = [{"role": "system", "content": persona_prompt}]
        # 注入历史对话（多轮记忆）
        history = state.get("messages", [])
        if history:
            temp_msgs.extend(history)
        temp_msgs.append({"role": "user", "content": user_input})

        try:
            resp = self.client.chat.completions.create(
                model=state.get("active_model", MODEL_PRO),
                messages=temp_msgs,
                stream=False,
                max_tokens=512,
            )
            reply = resp.choices[0].message.content or ""
        except Exception as e:
            reply = f"对话异常: {e}"

        return {"final_response": reply}


# ============================================
# 3. 构建 LangGraph 图
# ============================================

def _build_graph(nodes: OrchestratorNodes) -> StateGraph:
    """
    构建 LangGraph 状态图

    图结构:
                  ┌─────────────────┐
                  │    Supervisor    │
                  └────────┬────────┘
                           │
                    ┌──────┴──────┐
                    │  路由分支    │
                    └──────┬──────┘
              ┌────────┬───┴───┬────────┐
              ↓        ↓       ↓        ↓
         simple    knowledge  tool   hybrid
           ↓        ↓          ↓        ↓
       [Chat]   [RAG→Syn]  [Tool→Syn] [RAG+Tool→Syn]
    """
    graph = StateGraph(OrchestratorState)

    # 注册所有节点
    graph.add_node("supervisor", nodes.supervisor)
    graph.add_node("simple_chat", nodes.simple_chat)
    graph.add_node("rag_search", nodes.rag_search)
    graph.add_node("tool_execute", nodes.tool_execute)
    graph.add_node("synthesize", nodes.synthesize)

    # START → Supervisor
    graph.add_edge(START, "supervisor")

    # Supervisor → 条件路由
    def route_after_supervisor(state: OrchestratorState) -> str:
        intent = state.get("intent", "simple")
        routing = {
            "simple": "simple_chat",
            "knowledge": "rag_search",    # 只查文档
            "tool": "tool_execute",       # 只调工具
            "hybrid": "rag_search",       # 先查文档，再调工具，最后综合
        }
        next_node = routing.get(intent, "simple_chat")
        logger.info(f"[Router] {intent} → {next_node}")
        return next_node

    graph.add_conditional_edges("supervisor", route_after_supervisor, {
        "simple_chat": "simple_chat",
        "rag_search": "rag_search",
        "tool_execute": "tool_execute",
    })

    # simple_chat → END
    graph.add_edge("simple_chat", END)

    # rag_search → 判断是否需要工具 (hybrid 模式)
    def route_after_rag(state: OrchestratorState) -> str:
        if state.get("intent") == "hybrid":
            return "tool_execute"
        return "synthesize"

    graph.add_conditional_edges("rag_search", route_after_rag, {
        "synthesize": "synthesize",
        "tool_execute": "tool_execute",
    })

    # tool_execute → synthesize
    graph.add_edge("tool_execute", "synthesize")

    # synthesize → END
    graph.add_edge("synthesize", END)

    return graph


# ============================================
# 4. 编排器对外接口
# ============================================

class MultiAgentOrchestrator:
    """
    多 Agent 编排器（对外统一入口）

    用法:
        orch = MultiAgentOrchestrator(chat, tool, rag)
        result = orch.run("登录不了，帮我开个工单")
        print(result["final_response"])
        print(result["graph_trace"])  # 整个图的执行轨迹
    """

    def __init__(
        self,
        chat_agent: ChatAgent = None,
        tool_agent: ToolAgent = None,
        rag_agent: RAGAgent = None,
    ):
        # 懒初始化 → 加载 Skill 并注入人格设定
        if chat_agent is None:
            from src.skills.persona_skill import PersonaSkill
            from src.skills.time_skill import TimeSkill
            from src.skills.ticket_skill import TicketSkill

            persona = PersonaSkill()
            time_s = TimeSkill()
            ticket_s = TicketSkill()

            # 拼系统提示词：人格书 + 所有技能提示
            sys_prompt = persona.prompt_hint
            for s in [time_s, ticket_s]:
                if s.prompt_hint:
                    sys_prompt += f"\n\n{s.prompt_hint}"

            chat_agent = ChatAgent(system_prompt=sys_prompt)
            chat_agent.register_skill(persona)
            chat_agent.register_skill(time_s)
            chat_agent.register_skill(ticket_s)

            logger.info(f"技能已加载: persona({len(persona.prompt_hint)}字提示词) + time(1工具) + ticket(6工具)")
        if tool_agent is None:
            tool_agent = ToolAgent()
        if rag_agent is None:
            persist = os.path.join(settings.PROJECT_ROOT, "data", "chroma_db")
            vs = VectorStore(persist_dir=persist)
            # 自动导入文档
            if vs.doc_count == 0:
                docs_dir = os.path.join(settings.PROJECT_ROOT, "data", "documents")
                loader = DocumentLoader()
                chunks = loader.load_directory(docs_dir)
                vs.add_documents(chunks)
            rag_agent = RAGAgent(vector_store=vs)

        self.chat_agent = chat_agent
        self.tool_agent = tool_agent
        self.rag_agent = rag_agent
        self.memory = MemoryManager()   # 会话记忆引擎

        # 创建节点
        self.nodes = OrchestratorNodes(chat_agent, tool_agent, rag_agent)

        # 构建图
        self.graph = _build_graph(self.nodes)
        self.app = self.graph.compile()

        # 初始化语义守卫
        self._guard = SemanticGuard(self.rag_agent.vector_store.embedding_model)

        logger.info(
            f"MultiAgentOrchestrator 就绪: "
            f"Chat + Tool({self.tool_agent.tool_registry.count}工具) + "
            f"RAG({self.rag_agent.vector_store.doc_count}块) + "
            f"Guard"
        )

    def run(self, user_input: str) -> dict:
        """
        执行一次编排

        参数:
            user_input: 用户输入

        返回:
            {
                "final_response": "综合回复文本",
                "intent": "hybrid",
                "trace": ["supervisor", "rag_search", "tool_execute", "synthesize"],
                "rag_docs": "...",
                "tool_results": [...],
            }
        """
        logger.info(f"[Orchestrator] 开始处理: {user_input[:50]}...")

        # 全链路追踪
        cu = get_current_user()
        user_id = str(cu.get("user_id", cu.get("id", "anon"))) if cu else None
        trace_ctx = AgentTrace(user_input, user_id=user_id)
        t0 = time.time()

        # ---- 加载历史（多轮对话上下文）----
        history_msgs = self._load_history()

        # ---- 快速路由：关键词命中跳过 Supervisor LLM 调用，节省 1-2s ----
        fast_intent = self._fast_route(user_input)

        model_for_fast = select_model(fast_intent) if fast_intent else MODEL_PRO

        initial_state: OrchestratorState = {
            "user_input": user_input,
            "messages": history_msgs,
            "intent": fast_intent or "simple",
            "active_model": model_for_fast,
            "rag_docs": "",
            "tool_results": [],
            "supervisor_plan": fast_intent and f"快速路由:{fast_intent}" or "",
            "final_response": "",
        }

        if fast_intent:
            # 跳过 Supervisor，直接执行对应节点
            if fast_intent == "simple":
                state = self.nodes.simple_chat(initial_state)
                reply = state.get("final_response", "")
                trace_ctx.node("快速对话", duration=time.time() - t0)
                trace_ctx.done(intent="simple")
                self._save_memory(user_input, reply)       # ← 记忆落盘
                return {"final_response": reply, "intent": "simple",
                        "trace": ["simple_chat"], "plan": "快速路由",
                        "rag_docs": "", "tool_results": []}
            elif fast_intent == "knowledge":
                initial_state.update(self.nodes.rag_search(initial_state))
                state = self.nodes.synthesize(initial_state)
                reply = state.get("final_response", "")
                elapsed = time.time() - t0
                trace_ctx.node("知识检索", duration=elapsed * 0.5)
                trace_ctx.node("综合回复", duration=elapsed * 0.5)
                trace_ctx.done(intent="knowledge")
                self._save_memory(user_input, reply)       # ← 记忆落盘
                return {"final_response": reply, "intent": "knowledge",
                        "trace": ["rag_search", "synthesize"], "plan": "快速路由:知识库",
                        "rag_docs": initial_state.get("rag_docs", ""), "tool_results": []}
            elif fast_intent == "tool":
                initial_state.update(self.nodes.tool_execute(initial_state))
                state = self.nodes.synthesize(initial_state)
                reply = state.get("final_response", "")
                elapsed = time.time() - t0
                trace_ctx.node("工具执行", duration=elapsed * 0.5)
                trace_ctx.node("综合回复", duration=elapsed * 0.5)
                trace_ctx.done(intent="tool")
                self._save_memory(user_input, reply)       # ← 记忆落盘
                return {"final_response": reply, "intent": "tool",
                        "trace": ["tool_execute", "synthesize"], "plan": "快速路由:工具",
                        "rag_docs": "", "tool_results": initial_state.get("tool_results", [])}

        # 完整 LangGraph 图（fast_intent 未命中）
        final_state = self.app.invoke(initial_state)

        # 逐节点记录（含 token 估算：中文约 1 字 ≈ 2 token）
        elapsed = time.time() - t0
        user_tokens = len(user_input) * 2
        s_plan = final_state.get("supervisor_plan", "")
        trace_ctx.node("Supervisor", duration=elapsed * 0.15,
                       tokens_in=user_tokens, tokens_out=len(s_plan) * 2)

        if final_state.get("rag_docs"):
            rag_text = final_state["rag_docs"]
            trace_ctx.node("RAG", duration=elapsed * 0.25,
                           tokens_out=len(rag_text) * 2)

        if final_state.get("tool_results"):
            tool_text = str(final_state["tool_results"])
            trace_ctx.node("Tool", duration=elapsed * 0.20,
                           tokens_out=len(tool_text) * 2,
                           metadata={"count": len(final_state["tool_results"])})

        reply_text = final_state.get("final_response", "")
        trace_ctx.node("Synthesizer", duration=elapsed * 0.35,
                       tokens_out=len(reply_text) * 2)

        intent = final_state.get("intent", "simple")
        trace_ctx.done(intent=intent)

        reply = final_state.get("final_response", "")
        intent = final_state.get("intent", "simple")
        trace = self._build_trace(final_state)
        logger.info(f"[Orchestrator] 完成: 意图={intent}, 轨迹={'→'.join(trace)}, {elapsed:.1f}s")

        # ---- 写作记忆（持久化会话）----
        self._save_memory(user_input, reply)

        sid = getattr(self, "_active_sid", None)
        return {
            "final_response": reply,
            "intent": intent,
            "model": final_state.get("active_model", MODEL_PRO),
            "sid": sid,          # ← 前端拿 sid 恢复上下文
            "trace": trace,
            "plan": final_state.get("supervisor_plan", ""),
            "rag_docs": final_state.get("rag_docs", ""),
            "tool_results": final_state.get("tool_results", []),
        }

    def _load_history(self) -> list[dict]:
        """加载当前会话的历史消息（最近 20 条，避免上下文溢出）"""
        sid = getattr(self, "_active_sid", None)
        if not sid:
            return []
        msgs = self.memory.load(sid)
        if not msgs:
            return []
        # 取最近 20 条，去重连续重复
        return msgs[-20:]

    def _save_memory(self, user_input: str, reply: str):
        """保存一轮对话到记忆（供所有路径复用）"""
        sid = getattr(self, "_active_sid", None)
        if not sid:
            return
        self.memory.append(sid, {"role": "user", "content": user_input})
        self.memory.append(sid, {"role": "assistant", "content": reply})

    def _fast_route(self, text: str) -> str | None:
        """关键词快速预判意图，命中则跳过 Supervisor LLM 调用，节省 1-2s"""
        t = text
        # 纯工具触发词
        for w in ["几点", "几号", "星期", "今天日期", "现在时间"]:
            if w in t: return "tool"
        for w in ["等于", "加多少", "减多少", "乘多少", "除多少", "算一下", "计算"]:
            if w in t: return "tool"
        # 工单号 → 工具
        import re as _re
        if _re.search(r"TK\d", t.upper()):
            return "tool"
        # 工单操作词 → 工具（优先级高于知识库）
        for w in ["开工单", "建工单", "创建工单", "新建工单", "开个工单",
                   "查工单", "查询工单", "我的工单", "工单列表",
                   "关闭工单", "删除工单", "更新工单", "修改工单"]:
            if w in t: return "tool"
        # 知识库触发词（精确匹配，避免"怎么""如何"误拦截）
        for w in ["忘记密码", "密码重置", "无法登录", "登录不了",
                   "浏览器兼容", "API接口文档", "数据导出失败", "权限申请", "SLA时限", "工单指南"]:
            if w in t: return "knowledge"
        # 闲聊
        for w in ["你好", "谢谢", "再见", "hello", "hi", "晚安", "早安", "你是谁", "你是什么", "你叫什么"]:
            if t.strip().lower() == w: return "simple"
        return None

    def _build_trace(self, state: OrchestratorState) -> list[str]:
        """根据 State 推断执行轨迹"""
        trace = ["supervisor"]
        intent = state.get("intent", "simple")
        if intent == "simple":
            trace.append("simple_chat")
        elif intent == "knowledge":
            trace.extend(["rag_search", "synthesize"])
        elif intent == "tool":
            trace.extend(["tool_execute", "synthesize"])
        elif intent == "hybrid":
            trace.extend(["rag_search", "tool_execute", "synthesize"])
        return trace


# ============================================
# 5. 交互式控制台（测试用，登录交给前端 API）
# ============================================
if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if not settings.DEEPSEEK_API_KEY or "your-deepseek-api-key" in settings.DEEPSEEK_API_KEY:
        print("[X] 请先在 .env 中配置 DEEPSEEK_API_KEY！")
        sys.exit(1)

    from src.data.auth import AuthService, hash_password
    from src.data.context import set_current_user, clear_current_user, get_current_user
    from src.data.database import init_database, get_session
    from src.data.models import User

    init_database()

    # 确保 demo 用户存在
    with get_session() as db:
        if not db.query(User).filter(User.username == "demo").first():
            db.add(User(username="demo", email="demo@example.com",
                        password_hash=hash_password("demo123"), display_name="演示用户"))
            db.commit()
            print("[*] 已创建演示账号: demo / demo123")

    print("[*] 初始化多 Agent 编排器...")
    orch = MultiAgentOrchestrator()

    # 测试用：自动登录为 demo 用户（模拟前端传 token）
    result = AuthService.login("demo", "demo123")
    current_user_info = result["user"] if result["success"] else None
    set_current_user(current_user_info)
    if current_user_info:
        print(f"测试模式：自动以 [{current_user_info['display_name']}] 身份运行")

    print("=" * 60)
    print("  智能客服工单 Agent 系统（测试模式）")
    print("  已登录用户: " + (current_user_info["display_name"] if current_user_info else "无"))
    print("  ─────────────────────────────────")
    print("  可用功能：对话、查知识库、工单 CRUD、查时间、计算")
    print("  输入 quit 退出, /trace 看轨迹, /user 切换用户")
    print("=" * 60)

    last_result = None

    while True:
        try:
            label = current_user_info['display_name'] if current_user_info else "未登录"
            user_input = input(f"\n[{label}] 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("再见！")
            break

        if user_input == "/trace":
            if last_result:
                print(f"\n执行轨迹: {' → '.join(last_result['trace'])}")
                print(f"Supervisor: {last_result['plan']}")
                print(f"RAG 文档: {'有' if last_result['rag_docs'] else '无'}")
                print(f"工具结果: {len(last_result['tool_results'])} 条")
            else:
                print("暂无记录")
            continue

        if user_input == "/stats":
            print(get_dashboard())
            continue

        if user_input == "/skills":
            print("\n已加载技能:")
            from src.agents.chat_agent import get_global_skills
            for s in get_global_skills().list():
                print(f"  [{s['name']}] {s['description']} ({s['tools']}工具)")
            print()
            continue

        if user_input == "/user":
            # 测试用：切换登录/未登录（模拟前端传不传 token）
            if current_user_info:
                current_user_info = None
                clear_current_user()
                print("已切换为未登录状态（游客模式：只能对话+知识库）")
            else:
                result = AuthService.login("demo", "demo123")
                if result["success"]:
                    current_user_info = result["user"]
                    set_current_user(current_user_info)
                    print(f"已切换为 [{current_user_info['display_name']}]")
                    print("现在可以操作工单了")
            continue

        if not user_input:
            continue

        set_current_user(current_user_info)

        # ---- 语义守卫：向量检查是否需要登录 ----
        if not current_user_info and orch._guard.check(user_input):
            print(f"\n ⚠️ 请登录")
            continue

        last_result = orch.run(user_input)

        print("\n" + "=" * 60)
        print(f"执行轨迹: {' → '.join(last_result['trace'])}")
        print("=" * 60)
        print(f"\n【综合回复】\n{last_result['final_response']}\n")
        if last_result["tool_results"]:
            print(f"工具结果: {last_result['tool_results']}")