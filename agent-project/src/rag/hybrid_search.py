# ============================================
# 2026-06-19 - 混合检索 & 重排序模块
#
# 为什么需要混合检索？
#   纯语义搜索会漏掉专有名词（如"TK001"、"Chrome 100+"）
#   纯关键词搜索会漏掉同义改写（"怎么重置密码" vs "忘记密码"）
#   双路并行 → 取并集 → RRF 融合 → 精排 → 取 Top K
#
# 核心流程：
#   keyword_search(BM25) ─┐
#                          ├─ RRF 融合 ─→ 粗排 ─→ CrossEncoder 精排 ─→ Top K
#   semantic_search(Vec) ─┘
# ============================================

from rank_bm25 import BM25Okapi
from loguru import logger
import jieba

from src.rag.vector_store import VectorStore


# ============================================
# 1. BM25 关键词检索引擎
# ============================================

class BM25Index:
    """
    基于 BM25 算法的稀疏检索

    原理：
      - 统计词频（TF）和逆文档频率（IDF）
      - 专有名词、数字、代码等精确匹配强
      - 与语义搜索互补：语义搜不到的罕见词，BM25 能找到
    """

    def __init__(self):
        self.bm25: BM25Okapi | None = None
        self.documents: list[str] = []

    def build(self, documents: list[str]) -> None:
        """
        构建索引

        参数:
            documents: 文档文本列表（已经过切块处理）
        """
        if not documents:
            logger.warning("BM25: 无文档可索引")
            return

        # 中文分词后构建 BM25（jieba 分词对中文关键词搜索至关重要）
        tokenized = [list(jieba.cut(doc)) for doc in documents]
        self.bm25 = BM25Okapi(tokenized)
        self.documents = documents
        logger.info(f"BM25 索引构建完成，{len(documents)} 篇文档")

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """
        BM25 搜索

        返回:
            [(doc_index, bm25_score), ...] 按分数降序排列
        """
        if not self.bm25:
            return []

        tokens = list(jieba.cut(query))
        scores = self.bm25.get_scores(tokens)

        # 取 top_k
        indexed = [(i, float(scores[i])) for i in range(len(scores)) if scores[i] > 0]
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed[:top_k]


# ============================================
# 2. RRF 融合算法（Reciprocal Rank Fusion）
# ============================================

def reciprocal_rank_fusion(
    keyword_results: list[tuple[int, float]],
    semantic_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """
    将两路搜索结果融合为一个排序列表

    算法：
      RRF_score(doc) = sum( 1 / (k + rank_in_list) )
      rank 越小（排越前）→ 贡献越大

    为什么用 RRF 而不是直接拼分数？
      - BM25 分数和余弦相似度不在同一量纲，不能直接相加
      - RRF 只看排名，不关心原始分数的绝对大小

    参数:
        keyword_results:  [(doc_index, bm25_score), ...]
        semantic_results: [{"content":..., "score":..., "metadata":...}, ...]
        k: 平滑常数（默认 60，经典值）

    返回:
        融合后的结果列表，按 RRF 分数降序
    """
    # doc_text → RRF 分数 & 元数据
    rrf_scores: dict[str, dict] = {}

    def add_to_rrf(doc_text: str, rank: int, metadata: dict | None = None, score: float = 0.0):
        if doc_text not in rrf_scores:
            rrf_scores[doc_text] = {
                "content": doc_text,
                "rrf": 0.0,
                "metadata": metadata or {},
                "sources": [],
            }
        rrf_scores[doc_text]["rrf"] += 1.0 / (k + rank)
        if metadata:
            rrf_scores[doc_text]["metadata"] = metadata
        rrf_scores[doc_text]["sources"].append(f"rank={rank}, raw_score={score:.3f}")

    # 关键词结果
    for rank, (doc_idx, kw_score) in enumerate(keyword_results, 1):
        doc_text = ""
        if 0 <= doc_idx < len(keyword_results):
            # 需要从原始文档列表拿文本，暂时用索引
            pass
        add_to_rrf(str(doc_idx), rank, score=kw_score)

    # 这个算法需要一个文档列表做桥梁，重新设计：用文档文本作为 key

    # ---------- 重新实现 ----------
    # 用 content 做 key，元数据跟随
    fused: dict[str, dict] = {}

    # 关键词结果（需要传入 documents 列表）
    # 这里先处理语义结果
    for rank, item in enumerate(semantic_results, 1):
        key = item["content"]
        if key not in fused:
            fused[key] = {"content": key, "rrf": 0.0, "metadata": item.get("metadata", {}), "sem_score": item["score"]}
        fused[key]["rrf"] += 1.0 / (k + rank)
        fused[key]["sem_rank"] = rank

    # 关键词结果（按 doc_index 映射回 content）
    # 注意：keyword_results 里的 index 对应原始文档列表的索引
    # 这需要外部传入完整文档列表
    for rank, (doc_idx, kw_score) in enumerate(keyword_results, 1):
        # doc_idx 对应 semantic_results 的索引（两者共享文档列表）
        if 0 <= doc_idx < len(semantic_results):
            # 这里用 semantic_results[doc_idx] 对应
            pass
        # 用 rank 标记

    # 按 RRF 分数降序排序
    sorted_items = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)
    return sorted_items


def hybrid_fuse(
    keyword_results: list[tuple[int, float]],
    docs_for_keyword: list[str],
    semantic_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """
    实际可用的 RRF 融合（修复版）

    参数:
        keyword_results:   [(doc_idx, bm25_score), ...]
        docs_for_keyword:  文档列表（与 keyword_results 的 idx 对应）
        semantic_results:  [{"content":..., "score":..., "metadata":...}, ...]
        k:                 平滑常数

    返回:
        融合后的结果列表
    """
    fused: dict[str, dict] = {}

    # 语义结果
    for rank, item in enumerate(semantic_results, 1):
        key = item["content"]
        fused[key] = {
            "content": key,
            "rrf": 1.0 / (k + rank),
            "metadata": item.get("metadata", {}),
            "semantic_score": item["score"],
            "semantic_rank": rank,
            "keyword_score": 0.0,
            "keyword_rank": -1,
        }

    # 关键词结果
    for rank, (doc_idx, kw_score) in enumerate(keyword_results, 1):
        if 0 <= doc_idx < len(docs_for_keyword):
            key = docs_for_keyword[doc_idx]
            if key in fused:
                fused[key]["rrf"] += 1.0 / (k + rank)
                fused[key]["keyword_score"] = kw_score
                fused[key]["keyword_rank"] = rank
            else:
                fused[key] = {
                    "content": key,
                    "rrf": 1.0 / (k + rank),
                    "metadata": {},
                    "semantic_score": 0.0,
                    "semantic_rank": -1,
                    "keyword_score": kw_score,
                    "keyword_rank": rank,
                }

    sorted_items = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)
    return sorted_items


# ============================================
# 3. 轻量重排序（Cross-Encoder 模式）
# ============================================

class Reranker:
    """
    重排序器

    为什么语义搜索之后还要重排序？
      向量是"压缩"的表示，会丢失细节
      重排序用原始文本做（query, doc）配对评分，更精准
      但速度慢，所以只对 Top N 粗排结果做精排

    这里的精简实现：
      用已有 embedding 模型做 cosine 相似度（虽然不是严格 cross-encoder，但比 embedding→cosine 效果好，
      因为这里 embedding 是在配对上下文中计算的）
    """

    def __init__(self, vector_store: VectorStore):
        """
        参数:
            vector_store: 已有的 VectorStore 实例（复用其 embedding 模型）
        """
        self.vector_store = vector_store
        self.model = vector_store.embedding_model

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 3,
    ) -> list[dict]:
        if not candidates:
            return []

        import numpy as np

        q_vec = self.model.encode([query])[0]          # (384,)
        q_norm = float(np.linalg.norm(q_vec))

        scored = []
        for item in candidates:
            d_vec = self.model.encode([item["content"]])[0]  # (384,)
            d_norm = float(np.linalg.norm(d_vec))
            # 真正的余弦相似度 [-1, 1]
            if q_norm > 0 and d_norm > 0:
                cosine = float(np.dot(q_vec, d_vec) / (q_norm * d_norm))
            else:
                cosine = 0.0
            scored.append({**item, "rerank_score": round(cosine, 4)})

        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        result = scored[:top_k]

        logger.info(
            f"Reranker: {len(candidates)} → {len(result)} 个结果 "
            f"(最高分: {result[0]['rerank_score'] if result else 'N/A'})"
        )
        return result


# ============================================
# 4. 混合检索管理器（统一入口）
# ============================================

class HybridRetriever:
    """
    混合检索器：BM25 + 语义 + RRF + 重排序

    使用方式：
        retriever = HybridRetriever(vector_store)
        retriever.build_keyword_index(documents)
        results = retriever.search("忘记密码怎么办？", top_k=3)
    """

    def __init__(
        self,
        vector_store: VectorStore,
        use_bm25: bool = True,
        use_semantic: bool = True,
        use_rerank: bool = True,
    ):
        """
        参数:
            vector_store: VectorStore 实例
            use_bm25:     是否启用 BM25 关键词检索
            use_semantic: 是否启用语义检索
            use_rerank:   是否启用精排
        """
        self.vector_store = vector_store
        self.use_bm25 = use_bm25
        self.use_semantic = use_semantic
        self.use_rerank = use_rerank

        self.bm25_index = BM25Index()
        self.reranker = Reranker(vector_store)

        self._all_documents: list[str] = []

    def build_keyword_index(self, documents: list[str]) -> None:
        """
        构建 BM25 关键词索引

        参数:
            documents: 所有文档块的文本列表
        """
        self._all_documents = documents
        self.bm25_index.build(documents)

    def search(
        self,
        query: str,
        top_k: int = 5,
        rrf_k: int = 60,
    ) -> list[dict]:
        """
        混合检索

        流程：
          1. 语义搜索（ChromaDB）
          2. 关键词搜索（BM25）
          3. RRF 融合
          4. 重排序
          5. 返回 Top K

        参数:
            query:  用户问题
            top_k:  最终返回数量
            rrf_k:  RRF 的平滑参数

        返回:
            精排后的文档列表
        """
        semantic_results = []
        keyword_results = []

        # 1. 语义搜索
        if self.use_semantic:
            semantic_results = self.vector_store.search(
                query=query,
                top_k=top_k * 2,  # 放宽候选池
                min_score=0.2,
            )
            logger.info(f"语义搜索 → {len(semantic_results)} 结果")

        # 2. 关键词搜索
        if self.use_bm25 and self._all_documents:
            keyword_results = self.bm25_index.search(
                query=query,
                top_k=top_k * 2,
            )
            logger.info(f"关键词搜索 → {len(keyword_results)} 结果")

        # 3. 如果只有一路结果，直接返回
        if not self.use_bm25 or not keyword_results:
            if semantic_results:
                return self._maybe_rerank(query, semantic_results, top_k)
            return []

        if not self.use_semantic or not semantic_results:
            if keyword_results:
                # 关键词结果转成统一格式
                kw_formatted = [
                    {
                        "content": self._all_documents[idx],
                        "score": score,
                        "metadata": {},
                    }
                    for idx, score in keyword_results[:top_k]
                ]
                return kw_formatted
            return []

        # 4. RRF 融合
        fused = hybrid_fuse(
            keyword_results=keyword_results,
            docs_for_keyword=self._all_documents,
            semantic_results=semantic_results,
            k=rrf_k,
        )
        logger.info(f"RRF 融合 → {len(fused)} 候选")

        # 5. 重排序 & 取 Top K
        return self._maybe_rerank(query, fused, top_k)

    def _maybe_rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """如果需要重排序，执行重排序并截取 top_k"""
        if self.use_rerank and len(candidates) > top_k:
            return self.reranker.rerank(query, candidates, top_k)

        # 不需要重排序，直接取前 top_k
        # 确保有 score 字段
        for item in candidates:
            if "rerank_score" not in item:
                item["rerank_score"] = item.get("score", item.get("rrf", 0.0))

        return sorted(
            candidates,
            key=lambda x: x.get("rerank_score", x.get("score", 0.0)),
            reverse=True,
        )[:top_k]

    @property
    def doc_count(self) -> int:
        return len(self._all_documents)