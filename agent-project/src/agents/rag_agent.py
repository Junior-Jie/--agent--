# ============================================
# 2026-06-19 - RAG Agent（检索增强生成）
# 职责：连接向量检索 + LLM 对话，实现"基于文档回答问题"
#
# 核心流程：
#   用户问题 → 向量搜索知识库 → 取 Top K 结果
#   → 拼成提示词："请根据以下文档回答：{文档}\n问题：{用户问题}"
#   → 发给 DeepSeek → 模型基于文档生成答案
#
# 与普通 Agent 的区别：
#   ChatAgent: 模型凭记忆回答（可能瞎编）
#   RAG Agent: 模型根据你提供的文档回答（有据可查）
# ============================================

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from loguru import logger
from openai.types.chat import ChatCompletionMessage

from src.agents.chat_agent import ChatAgent
from src.agents.tool_agent import ToolAgent
from src.rag.vector_store import VectorStore
from src.rag.document_loader import DocumentLoader
from src.rag.hybrid_search import HybridRetriever
from src.tools.base import ToolRegistry
from src.tools.builtin_tools import create_tool_registry
from src.config.settings import settings


class RAGAgent(ToolAgent):
    """
    RAG Agent：继承 ToolAgent（保持工具调用能力），新增文档检索能力

    双重能力：
      1. 工具调用：查时间、算数、查工单、搜 FAQ（继承自 ToolAgent）
      2. 文档检索：从知识库搜索相关文档片段，基于原文回答（新增）

    RAG 有效性保证：
      - 搜索不到内容时，明确告知用户"未找到相关文档"
      - 文档内容不足以回答时，让模型诚实说"文档未涉及此问题"
    """

    def __init__(
        self,
        system_prompt: str = None,
        tool_registry: ToolRegistry = None,
        vector_store: VectorStore = None,
        use_hybrid: bool = True,
        search_top_k: int = 3,
        search_min_score: float = 0.3,
    ):
        """
        初始化

        参数:
            system_prompt:    系统提示词
            tool_registry:    工具注册中心
            vector_store:     向量数据库（传入已建好的，避免重复加载）
            use_hybrid:       是否启用混合检索（BM25 + 语义 + RRF + 精排）
            search_top_k:     每次检索返回的文档块数
            search_min_score: 最低相似度阈值
        """
        if system_prompt is None:
            system_prompt = self._build_system_prompt()

        if tool_registry is None:
            tool_registry = create_tool_registry()

        super().__init__(system_prompt=system_prompt, tool_registry=tool_registry)

        # 向量库
        if vector_store is None:
            persist_dir = os.path.join(settings.PROJECT_ROOT, "data", "chroma_db")
            vector_store = VectorStore(persist_dir=persist_dir)

        self.vector_store = vector_store
        self.use_hybrid = use_hybrid
        self.search_top_k = search_top_k
        self.search_min_score = search_min_score

        # 混合检索器（延迟构建：等文档导入后再建 BM25 索引）
        self.hybrid_retriever: HybridRetriever | None = None
        if use_hybrid and vector_store.doc_count > 0:
            self._build_hybrid_index()

    @property
    def name(self) -> str:
        return "知识库 RAG Agent"

    @property
    def description(self) -> str:
        return "基于企业文档知识库回答，适合产品使用、政策流程、FAQ 类问题"

        logger.info(
            f"RAGAgent 就绪: 知识库={vector_store.doc_count}块, "
            f"混合检索={'开' if use_hybrid else '关'}, TopK={search_top_k}"
        )

    def _build_hybrid_index(self):
        """从向量库拉取全量文档，构建 BM25 索引"""
        docs = self.vector_store.get_all_texts()
        self.hybrid_retriever = HybridRetriever(
            self.vector_store,
            use_bm25=True,
            use_semantic=True,
            use_rerank=True,
        )
        self.hybrid_retriever.build_keyword_index(docs)
        logger.info(f"混合检索索引构建完成，{len(docs)} 篇文档")

    def _build_system_prompt(self) -> str:
        """构建 RAG 专用的系统提示词"""
        return (
            "你是一个企业知识库助手，负责根据提供的文档内容回答用户问题。\n\n"
            "回答规则：\n"
            "1. 优先基于文档内容回答，文档有据可查时引用原文关键信息\n"
            "2. 如果文档包含答案但不够完整，补充你的理解但要标注「以下为补充建议」\n"
            "3. 如果文档完全不涉及这个问题，诚实告知用户「当前知识库未收录该内容」\n"
            "4. 如果用户的问题与文档无关（如闲聊），正常回答即可\n"
            "5. 你也可以使用工具（查时间、计算、查工单）来辅助回答\n"
            "6. 回答使用中文，简洁清晰"
        )

    # ========================================
    # 核心：RAG 对话流程
    # ========================================

    def chat(self, user_input: str, stream: bool = True) -> str:
        """
        RAG 对话入口

        流程：
          1. 先检索知识库
          2. 把搜索结果拼到用户消息前面
          3. 交给模型回答
          4. 模型也能同时调用工具（继承自 ToolAgent 的循环）
        """
        # Step 1: 检索
        doc_context = self._retrieve_context(user_input)

        # Step 2: 构建增强后的用户消息
        if doc_context:
            # 有检索结果：拼上文档上下文
            augmented_input = (
                f"【参考文档】\n"
                f"{doc_context}\n\n"
                f"【用户问题】\n"
                f"{user_input}"
            )
            logger.info(
                f"RAG 增强: 检索到 {len(doc_context.split(chr(10)))} 行上下文"
            )
        else:
            # 无结果：原样发送
            augmented_input = user_input
            logger.info("RAG 无检索结果，原样发送")

        # Step 3: 调用父类 ToolAgent 的 chat（保留工具调用能力）
        #         但消息需要特殊处理：把文档上下文拼进去
        #         这里覆盖 user_input，让父类按增强后的消息发给模型
        return self._chat_with_context(augmented_input, user_input, stream)

    def _chat_with_context(
        self,
        augmented_input: str,
        original_input: str,
        stream: bool,
    ) -> str:
        """
        带文档上下文的对话（复用 ToolAgent 的工具调用循环）

        为什么不直接调 super().chat()？
          因为 RAG 的上下文只影响当前这一轮，不应该永久留在 messages 里
          如果留在 messages 里，下一轮对话也会带着上一轮的文档片段
        """
        # 1. 把增强输入加入历史
        self.messages.append({"role": "user", "content": augmented_input})
        logger.info(f"发送 RAG 增强消息: {original_input[:30]}...")

        max_rounds = 5
        for _ in range(max_rounds):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tool_registry.get_schemas(),
                tool_choice="auto",
                stream=False,
                temperature=0.7,
                max_tokens=2048,
            )

            msg: ChatCompletionMessage = response.choices[0].message

            # 无工具调用 → 输出答案
            if not msg.tool_calls:
                reply = msg.content or ""
                if stream:
                    print("\n" + "=" * 50)
                    print("【RAG Agent 回复】")
                    print("=" * 50)
                    print(reply)
                    print("=" * 50 + "\n")
                self.messages.append({"role": "assistant", "content": reply})
                return reply

            # 有工具调用 → 执行
            print("\n" + "-" * 40)
            tool_results = []
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments)
                print(f"[Tool] {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")

                result = self.tool_registry.execute(tool_name, tool_args)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
                print(f"[Tool] 返回: {str(result)[:200]}")
            print("-" * 40)

            # 加入历史
            self.messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            self.messages.extend(tool_results)

        reply = "处理超时，请简化问题。"
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    # ========================================
    # 检索逻辑
    # ========================================

    def _retrieve_context(self, query: str) -> str:
        """
        从知识库检索相关文档片段

        返回:
            格式化的文档上下文文本，或空字符串
        """
        # 混合检索优先
        if self.use_hybrid and self.hybrid_retriever:
            results = self.hybrid_retriever.search(query=query, top_k=self.search_top_k)
        else:
            results = self.vector_store.search(
                query=query,
                top_k=self.search_top_k,
                min_score=self.search_min_score,
            )

        if not results:
            return ""

        parts = []
        for i, r in enumerate(results, 1):
            meta = r.get("metadata", {})
            source = meta.get("source", "未知")
            title = meta.get("title", source)
            score = r.get("rerank_score") or r.get("score") or 0.0

            parts.append(
                f"--- 片段 {i}（来源：{title}，相关度：{score:.0%}）---\n"
                f"{r['content']}"
            )

        return "\n\n".join(parts)

    # ========================================
    # 知识库管理
    # ========================================

    def import_documents(self, dir_path: str = None) -> dict:
        """
        导入文档到知识库

        参数:
            dir_path: 文档目录，默认 data/documents/

        返回:
            {"files": 3, "chunks": 9, "imported": 9}
        """
        if dir_path is None:
            dir_path = os.path.join(settings.PROJECT_ROOT, "data", "documents")

        loader = DocumentLoader()
        chunks = loader.load_directory(dir_path)
        imported = self.vector_store.add_documents(chunks)

        stats = {
            "files": len(set(c["metadata"]["source"] for c in chunks)),
            "chunks": len(chunks),
            "imported": imported,
        }

        # 导入后重建 BM25 索引
        if self.use_hybrid:
            self._build_hybrid_index()

        logger.info(f"文档导入完成: {stats}")
        return stats

    def rebuild_knowledge_base(self, dir_path: str = None) -> dict:
        """重建知识库（清空 + 重新导入），自动刷新 BM25 索引"""
        self.vector_store.clear()
        return self.import_documents(dir_path)

    def get_kb_stats(self) -> dict:
        """获取知识库统计"""
        return {
            "total_chunks": self.vector_store.doc_count,
            "hybrid_active": self.use_hybrid and self.hybrid_retriever is not None,
            "search_top_k": self.search_top_k,
            "min_score": self.search_min_score,
        }


# ============================================
# 交互式控制台
# ============================================
if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if not settings.DEEPSEEK_API_KEY or "your-deepseek-api-key" in settings.DEEPSEEK_API_KEY:
        print("[X] 请先在 .env 中配置 DEEPSEEK_API_KEY！")
        sys.exit(1)

    # 初始化向量库
    persist_dir = os.path.join(settings.PROJECT_ROOT, "data", "chroma_db")
    vs = VectorStore(persist_dir=persist_dir)

    # 如果知识库为空，自动导入文档
    if vs.doc_count == 0:
        print("[!] 知识库为空，正在导入文档...")
        docs_dir = os.path.join(settings.PROJECT_ROOT, "data", "documents")
        loader = DocumentLoader()
        chunks = loader.load_directory(docs_dir)
        vs.add_documents(chunks)

    # 创建 RAGAgent（自动构建混合检索索引）
    agent = RAGAgent(vector_store=vs)

    print("=" * 50)
    print("  RAG 知识库助手 (DeepSeek V4 Pro)")
    print(f"  知识库: {vs.doc_count} 个文档块")
    print(f"  检索: {'混合(BM25+语义+精排)' if agent.hybrid_retriever else '纯语义'}")
    print(f"  可用工具: {agent.tool_registry.list_names()}")
    print("  输入 quit 退出, reset 重置对话")
    print("  输入 /rebuild 重建知识库, /stats 查看状态")
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
            agent.reset()
            print("[OK] 对话已重置")
            continue

        if user_input == "/rebuild":
            print("[*] 重建知识库...")
            docs_dir = os.path.join(settings.PROJECT_ROOT, "data", "documents")
            result = agent.rebuild_knowledge_base(docs_dir)
            print(f"[OK] 已导入 {result['imported']} 个文档块")
            continue

        if user_input == "/stats":
            stats = agent.get_kb_stats()
            print(f"知识库统计: {stats}")
            continue

        if not user_input:
            continue

        agent.chat(user_input)