# ============================================
# 2026-06-19 - ChromaDB 向量存储模块
# 职责：把文本块转成向量 → 存到 ChromaDB → 支持语义搜索
#
# 核心概念：
#   Embedding：把"苹果好吃"变成 [0.1, -0.3, 0.7, ...]
#   语义相近的文本，向量也相近 → "苹果美味"接近 "苹果好吃"
#   → 用户搜"水果"也能找到"苹果"相关内容（关键词搜不到）
# ============================================

import os
import warnings

# 屏蔽 HuggingFace 相关的警告
warnings.filterwarnings("ignore", category=UserWarning, module="huggingface_hub")
warnings.filterwarnings("ignore", category=FutureWarning)

from loguru import logger

import chromadb
from chromadb.config import Settings as ChromaSettings

# 本地 embedding 模型（免费，不消耗 API）
from sentence_transformers import SentenceTransformer


class VectorStore:
    """
    ChromaDB 向量数据库封装

    工作流程：
      存入：文本 → Embedding模型 → 向量 → ChromaDB（持久化到磁盘）
      查询：问题 → Embedding模型 → 向量 → ChromaDB找相似 → 返回最相关文本
    """

    EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

    # 模型本地缓存目录（首次从 hf-mirror 下载后，后续从本地加载，不被墙）
    MODEL_CACHE_BASE = os.path.expanduser(
        r"~\.cache\huggingface\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2"
    )

    def _find_local_model_path(self) -> str | None:
        """在本地缓存中查找已下载的模型路径"""
        if not os.path.isdir(self.MODEL_CACHE_BASE):
            return None
        snapshots = os.path.join(self.MODEL_CACHE_BASE, "snapshots")
        if not os.path.isdir(snapshots):
            return None
        dirs = os.listdir(snapshots)
        if dirs:
            return os.path.join(snapshots, dirs[0])
        return None

    def _load_model(self) -> SentenceTransformer:
        """加载 embedding 模型（优先本地缓存）"""
        local = self._find_local_model_path()
        if local:
            logger.info(f"从本地缓存加载: {local}")
            return SentenceTransformer(local, local_files_only=True)
        # 首次：设置镜像并通过网络下载
        logger.info(f"首次下载模型: {self.EMBEDDING_MODEL_NAME} (使用 hf-mirror.com)...")
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        return SentenceTransformer(self.EMBEDDING_MODEL_NAME)

    def __init__(
        self,
        persist_dir: str = "data/chroma_db",
        collection_name: str = "knowledge_base",
    ):
        # 1. Embedding 模型
        logger.info(f"正在加载 Embedding 模型: {self.EMBEDDING_MODEL_NAME}...")
        self.embedding_model = self._load_model()
        dim = self.embedding_model.get_sentence_embedding_dimension()
        logger.info(f"Embedding 模型加载完成，向量维度: {dim}")

        # 2. ChromaDB 客户端
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)

        self.chroma_client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # 3. 获取或创建集合
        # hnsw:space=cosine → 使用余弦距离（范围 [0,2]，0=完全相同，2=完全相反）
        self.collection_name = collection_name
        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={
                "description": "智能云平台知识库",
                "hnsw:space": "cosine",
            },
        )

        logger.info(
            f"VectorStore 就绪: 集合={collection_name}, "
            f"已有文档数={self.collection.count()}"
        )

    def add_documents(
        self,
        chunks: list[dict],
        batch_size: int = 100,
        max_chunks: int = 10000,
    ) -> int:
        """
        将文本块向量化后分批存入 ChromaDB

        参数:
            chunks:     文本块列表
            batch_size: 每批处理的块数（避免一次性向量化太多导致内存飙升）
            max_chunks: 知识库总块数上限（超过拒绝导入）
        """
        if not chunks:
            logger.warning("没有可添加的文档块")
            return 0

        # 容量检查
        current = self.collection.count()
        if current >= max_chunks:
            logger.error(
                f"知识库已达到上限 {max_chunks} 块（当前 {current}），"
                f"如需扩容请修改 max_chunks 参数"
            )
            return 0

        # 截断到剩余容量
        remaining = max_chunks - current
        if len(chunks) > remaining:
            logger.warning(
                f"待导入 {len(chunks)} 块，但只剩 {remaining} 容量，截断处理"
            )
            chunks = chunks[:remaining]

        # 分批向量化（防止一次性 encode 10000 条撑爆内存）
        total = 0
        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start:batch_start + batch_size]

            ids = []
            texts = []
            metadatas = []
            for chunk in batch:
                source = chunk["metadata"]["source"]
                idx = chunk["metadata"]["chunk_index"]
                ids.append(f"{source}#chunk{idx}")
                texts.append(chunk["content"])
                metadatas.append(chunk["metadata"])

            logger.info(
                f"向量化批次 {batch_start // batch_size + 1}: "
                f"{batch_start + 1}-{batch_start + len(batch)} / {len(chunks)}"
            )
            embeddings = self.embedding_model.encode(texts).tolist()

            self.collection.upsert(
                ids=ids, embeddings=embeddings,
                documents=texts, metadatas=metadatas,
            )
            total += len(batch)

        logger.info(
            f"成功存入 {total} 个文档块（知识库总计 {self.collection.count()} 块）"
        )
        return total

    def search(
        self, query: str, top_k: int = 3, min_score: float = 0.0,
    ) -> list[dict]:
        """
        语义搜索

        参数:
            query:     用户问题
            top_k:     返回最相关的 K 个结果
            min_score: 最低相似度阈值（0-1）
        """
        if self.collection.count() == 0:
            logger.warning("向量库为空，请先导入文档")
            return []

        query_embedding = self.embedding_model.encode([query]).tolist()

        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        formatted = []
        if results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i]
                similarity = 1 - (distance / 2)  # 余弦距离 → 相似度
                if similarity >= min_score:
                    formatted.append({
                        "content": doc,
                        "score": round(similarity, 3),
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    })

        logger.info(f"搜索 '{query[:30]}' → {len(formatted)} 个结果 (top {top_k})")
        return formatted

    def clear(self):
        """清空集合"""
        self.chroma_client.delete_collection(self.collection_name)
        self.collection = self.chroma_client.get_or_create_collection(
            name=self.collection_name,
            metadata={"description": "智能云平台知识库", "hnsw:space": "cosine"},
        )
        logger.info(f"集合 {self.collection_name} 已清空")

    def get_all_documents(self) -> list[dict]:
        """
        获取知识库中所有文档块（用于 BM25 索引构建）

        返回:
            [{"content": "...", "metadata": {...}}, ...]
        """
        if self.collection.count() == 0:
            return []
        results = self.collection.get(include=["documents", "metadatas"])
        docs = []
        if results["documents"]:
            for i, doc in enumerate(results["documents"]):
                docs.append({
                    "content": doc,
                    "metadata": results["metadatas"][i] if results["metadatas"] else {},
                })
        return docs

    def get_all_texts(self) -> list[str]:
        """获取知识库中所有文档的纯文本列表"""
        docs = self.get_all_documents()
        return [d["content"] for d in docs]

    @property
    def doc_count(self) -> int:
        return self.collection.count()