# ============================================
# 2026-06-19 - 可观测性追踪模块
# 职责：全链路追踪 + Token 统计 + 耗时统计
#
# 两种模式：
#   1. 本地模式（默认）：log 输出 + 内存累计统计
#      无需任何外部服务，开箱即用
#
#   2. Langfuse 模式：配置 LANG_FUSE_SECRET_KEY
#      所有追踪推送到 Langfuse 云面板，可视化分析
#
# 切换到 Langfuse：
#   1. 去 https://cloud.langfuse.com 注册（免费）
#   2. .env 里配置 LANG_FUSE_SECRET_KEY 和 LANG_FUSE_PUBLIC_KEY
#   3. 重启服务
# ============================================

import time
import os
import functools
from datetime import datetime
from contextlib import contextmanager
from loguru import logger

# Langfuse 可选依赖
try:
    import langfuse as _lf
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False


# ============================================
# 1. 本地追踪器（无需 Langfuse）
# ============================================

class LocalTracer:
    """
    本地追踪器：记录每次调用的耗时、token、结果

    存储在内存中，重启后丢失。适合开发调试。
    """

    def __init__(self, max_records: int = 1000):
        self.max_records = max_records
        self.traces: list[dict] = []  # 完整会话记录
        self.stats: dict = {           # 累计统计
            "total_requests": 0,
            "total_tokens": 0,
            "total_time_sec": 0.0,
            "errors": 0,
            "tool_calls": 0,
            "by_intent": {},
        }

    def start_trace(self, user_input: str, user_id: str | None = None) -> str:
        """开始一条追踪，返回 trace_id"""
        trace_id = f"trace_{int(time.time() * 1000000)}"
        self.traces.append({
            "id": trace_id,
            "user_input": user_input[:200],
            "user_id": user_id or "anonymous",
            "start_time": time.time(),
            "nodes": [],
            "errors": [],
        })
        # 裁剪旧记录
        if len(self.traces) > self.max_records:
            self.traces = self.traces[-self.max_records:]
        return trace_id

    def add_node(self, trace_id: str, node_name: str, start: float,
                 end: float, tokens_in: int = 0, tokens_out: int = 0,
                 metadata: dict = None):
        """记录一个节点的执行"""
        duration = end - start
        for t in self.traces:
            if t["id"] == trace_id:
                t["nodes"].append({
                    "name": node_name,
                    "duration": round(duration, 3),
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "metadata": metadata or {},
                })
                # 更新统计
                self.stats["total_requests"] = max(
                    self.stats["total_requests"], len(self.traces)
                )
                self.stats["total_tokens"] += tokens_in + tokens_out
                self.stats["total_time_sec"] += duration
                break

    def end_trace(self, trace_id: str, intent: str = "", error: str = ""):
        """结束追踪"""
        for t in self.traces:
            if t["id"] == trace_id:
                t["end_time"] = time.time()
                t["total_duration"] = round(t["end_time"] - t["start_time"], 3)
                t["intent"] = intent
                if error:
                    t["errors"].append(error)
                    self.stats["errors"] += 1
                # 更新意图统计
                if intent:
                    self.stats["by_intent"][intent] = \
                        self.stats["by_intent"].get(intent, 0) + 1
                break

    def get_latest_trace(self):
        """获取最新一条追踪记录"""
        return self.traces[-1] if self.traces else None

    def get_summary(self) -> dict:
        """获取统计摘要"""
        traces = len(self.traces)
        avg_time = self.stats["total_time_sec"] / max(traces, 1)
        return {
            "total_traces": traces,
            "avg_duration_sec": round(avg_time, 2),
            "total_tokens": self.stats["total_tokens"],
            "errors": self.stats["errors"],
            "by_intent": dict(self.stats["by_intent"]),
        }


# 全局单例
_local_tracer = LocalTracer()


# ============================================
# 2. Langfuse 集成（可选）
# ============================================

_langfuse_client = None


def _get_langfuse():
    """懒初始化 Langfuse 客户端"""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    if not LANGFUSE_AVAILABLE:
        return None

    secret = os.getenv("LANG_FUSE_SECRET_KEY", "")
    public = os.getenv("LANG_FUSE_PUBLIC_KEY", "")

    if not secret or not public:
        return None

    try:
        _langfuse_client = _lf.Langfuse(
            secret_key=secret,
            public_key=public,
        )
        logger.info("Langfuse 已连接")
        return _langfuse_client
    except Exception as e:
        logger.warning(f"Langfuse 连接失败，使用本地追踪: {e}")
        return None


def langfuse_enabled() -> bool:
    """检查 Langfuse 是否可用"""
    return _get_langfuse() is not None


# ============================================
# 3. @trace_node 装饰器
# ============================================

def trace_node(name: str = None, track_tokens: bool = True):
    """
    节点追踪装饰器

    用法：
        @trace_node("Supervisor")
        def supervisor(state):
            ...

    自动记录：耗时、token 估算、异常
    """
    node_name = name

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            node = node_name or func.__name__

            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start

                # 估算 tokens（粗略：中文 1 字 ≈ 2 token）
                tokens_est = 0
                if track_tokens and isinstance(result, dict):
                    for val in result.values():
                        if isinstance(val, str):
                            tokens_est = max(tokens_est, len(val) * 2)

                logger.info(f"[{node}] {elapsed:.2f}s, ~{tokens_est}t")

                # 如果有 Langfuse，推送到云端
                lf = _get_langfuse()
                if lf:
                    try:
                        trace = lf.trace(name=node)
                        trace.generation(
                            name=node,
                            model="deepseek-v4-pro",
                            usage={
                                "input": tokens_est,
                                "output": 0,
                            },
                        )
                    except Exception:
                        pass

                return result

            except Exception as e:
                elapsed = time.time() - start
                logger.error(f"[{node}] FAILED after {elapsed:.2f}s: {e}")
                raise

        return wrapper
    return decorator


# ============================================
# 4. Agent 追踪上下文管理器
# ============================================

class AgentTrace:
    """
    Agent 全链路追踪

    用法：
        trace = AgentTrace(user_input, user_id)
        trace.node("Supervisor", supervisor_result)
        trace.node("RAG", rag_result)
        trace.done(final_state)
    """

    def __init__(self, user_input: str, user_id: str = None):
        self.input = user_input[:200]
        self.user_id = user_id
        self.nodes: list[dict] = []
        self.start = time.time()
        self.trace_id = _local_tracer.start_trace(user_input, user_id)

        # Langfuse（可选）
        self._lf_trace = None
        lf = _get_langfuse()
        if lf:
            try:
                self._lf_trace = lf.trace(
                    name="agent-request",
                    input=user_input[:500],
                    user_id=user_id,
                )
            except Exception:
                pass

    def node(self, name: str, duration: float = 0,
             metadata: dict = None, error: str = None,
             tokens_in: int = 0, tokens_out: int = 0):
        """记录一个节点的执行"""
        self.nodes.append({
            "name": name, "duration": round(duration, 3),
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "metadata": metadata or {}, "error": error,
        })
        _local_tracer.add_node(
            self.trace_id, name, time.time() - duration, time.time(),
            metadata=metadata,
        )

        if self._lf_trace:
            try:
                gen = self._lf_trace.generation(name=name)
                if error:
                    gen.end(level="ERROR", status_message=error)
                else:
                    gen.end()
            except Exception:
                pass

    def done(self, intent: str = "", error: str = ""):
        """标记追踪完成"""
        total = round(time.time() - self.start, 3)
        total_tokens = sum(n.get("tokens_in", 0) + n.get("tokens_out", 0) for n in self.nodes)
        _local_tracer.end_trace(self.trace_id, intent=intent, error=error)
        _local_tracer.stats["total_tokens"] += total_tokens
        for t in _local_tracer.traces:
            if t["id"] == self.trace_id:
                t["nodes"] = self.nodes
                t["total_tokens"] = total_tokens
                break
        logger.info(
            f"[Trace] {intent or '?'} | {len(self.nodes)}节点 | {total}s | ~{total_tokens}t"
        )

        if self._lf_trace:
            try:
                self._lf_trace.update(output={"intent": intent, "nodes": len(self.nodes)})
            except Exception:
                pass
            # 确保推送
            _get_langfuse().flush()


# ============================================
# 5. 控制台查询命令
# ============================================

def get_dashboard() -> str:
    """
    控制台仪表盘（给 /stats 命令用）

    返回格式化的统计信息
    """
    s = _local_tracer.get_summary()
    latest = _local_tracer.get_latest_trace()

    # 意图名称翻译
    intent_names = {
        "simple": "闲聊", "knowledge": "查知识库", "tool": "调工具", "hybrid": "混合操作",
    }
    by_intent_cn = {}
    for k, v in s.get("by_intent", {}).items():
        by_intent_cn[intent_names.get(k, k)] = v

    # 节点名称翻译
    node_names = {
        "Supervisor": "调度分析", "RAG": "知识检索",
        "Tool": "工具执行", "Synthesizer": "综合回复",
        "simple_chat": "简单对话",
    }

    lines = [
        "═" * 40,
        "  运行仪表盘",
        "═" * 40,
        f"  已处理: {s['total_traces']} 次请求",
        f"  平均耗时: {s['avg_duration_sec']}s",
        f"  累计 Token: {s['total_tokens']}",
        f"  错误: {s['errors']} 次",
        f"  追踪模式: {'Langfuse 云端' if langfuse_enabled() else '本地记录'}",
    ]

    if by_intent_cn:
        parts = ", ".join(f"{k}:{v}" for k, v in by_intent_cn.items())
        lines.append(f"  请求类型: {parts}")

    if latest:
        lines.extend([
            "",
            "  最近一次请求:",
            f"    用户说: {latest['user_input'][:60]}",
            f"    耗时: {latest.get('total_duration', '?')}s",
            f"    Token: ~{latest.get('total_tokens', 0)}",
        ])
        for node in latest.get("nodes", []):
            label = node_names.get(node["name"], node["name"])
            lines.append(
                f"      {label}  {node['duration']}s"
            )

    lines.append("═" * 40)
    return "\n".join(lines)