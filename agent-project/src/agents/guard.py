# ============================================
# 2026-06-20 - 语义守卫层（关键词优先 + 语义兜底）
# 关键词匹配：快、准、不误判
# 语义匹配：兜底「帮我提交个问题」这类变体说法
# ============================================

import numpy as np
from loguru import logger


class SemanticGuard:
    """语义守卫：关键词优先，向量兜底"""

    # 显式工单操作关键词（精确命中→直接拦截）
    TICKET_KEYWORDS = [
        "开工单", "创建工单", "新建工单", "帮我开个工单", "帮我开一个工单",
        "提交工单", "帮我提交", "修改工单", "更新工单", "关闭工单",
        "删除工单", "撤销工单", "取消工单", "转单",
        "查工单", "查看工单", "工单列表", "我的工单", "我提交的工单",
        "查询工单", "帮我查工单",
    ]

    def __init__(self, embedding_model):
        self.model = embedding_model
        # 语义兜底向量（覆盖非关键词语法）
        semantic_patterns = [
            "帮我提交一个问题", "帮我上报一个故障", "我要反馈一个bug",
        ]
        self._sem_vecs = self.model.encode(semantic_patterns) if semantic_patterns else None
        logger.info(f"Guard 就绪: {len(self.TICKET_KEYWORDS)} 关键词 + 语义兜底")

    def check(self, user_input: str) -> bool:
        """返回 True = 需要登录"""
        # 策略 1: 关键词精确匹配（0ms，不漏不误）
        for kw in self.TICKET_KEYWORDS:
            if kw in user_input:
                return True

        # 策略 2: 语义兜底（覆盖变体说法）
        if self._sem_vecs is not None:
            v = self.model.encode([user_input])
            sim = self._cosine_sim(v, self._sem_vecs)
            if sim >= 0.60:      # 高阈值，只拦明显的变体
                return True

        return False

    @staticmethod
    def _cosine_sim(a, B):
        a_n = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
        B_n = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-10)
        return float(np.max(np.dot(a_n, B_n.T)))