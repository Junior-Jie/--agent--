# ============================================
# 2026-06-19 - MCP Client（模型上下文协议客户端）
# 职责：连接外部 MCP Server → 发现工具 → 注入 ToolRegistry
# 架构：后台线程跑 asyncio event loop，同步调用通过桥接器投递
# ============================================

import asyncio
import threading
from contextlib import AsyncExitStack
from concurrent.futures import Future
from typing import Any
from loguru import logger

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from src.tools.base import Tool, ToolRegistry


# ============================================
# 1. 后台事件循环桥接器
# ============================================

class MCPSessionBridge:
    """在后台线程跑 asyncio loop，把异步操作变成同步"""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("MCP 后台线程已启动")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_coro(self, coro) -> Any:
        """在后台线程运行协程，同步返回结果"""
        if not self._loop or not self._loop.is_running():
            raise RuntimeError("MCP 后台线程未运行")
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)


_bridge: MCPSessionBridge | None = None


def get_bridge() -> MCPSessionBridge:
    global _bridge
    if _bridge is None:
        _bridge = MCPSessionBridge()
        _bridge.start()
    return _bridge


# ============================================
# 2. MCP 工具包装器
# ============================================

class MCPToolWrapper:
    """将 MCP 工具包装为同步可调用的 Tool"""

    def __init__(self, tool_def: dict, session: ClientSession, bridge: MCPSessionBridge):
        self.name = tool_def["name"]
        self.description = tool_def.get("description", "")
        self.input_schema = tool_def.get("inputSchema", {"type": "object", "properties": {}, "required": []})
        self.session = session
        self.bridge = bridge

    def to_tool(self) -> Tool:
        return Tool(
            name=f"mcp_{self.name}",
            description=f"[MCP] {self.description}",
            parameters=self.input_schema,
            func=self._call_sync,
        )

    def _call_sync(self, **kwargs) -> str:
        """同步调用 MCP 工具"""
        try:
            result = self.bridge.run_coro(
                self.session.call_tool(self.name, arguments=kwargs)
            )
            if result.content:
                for c in result.content:
                    if hasattr(c, "text"):
                        return c.text
                return str(result.content)
            return str(result)
        except Exception as e:
            return f"[MCP Error] {self.name}: {e}"


# ============================================
# 3. MCP 客户端管理器（持久连接）
# ============================================

class MCPClientManager:
    """
    管理多个 MCP 服务器连接

    关键设计：
      - 用 AsyncExitStack 保持连接活跃
      - 发现和调用共用一个持久 Session
      - 工具注册到 ToolRegistry 后可以随时调用
    """

    def __init__(self):
        self.bridge = get_bridge()
        self._servers: dict[str, dict] = {}
        self._discovered_tools: list[Tool] = []
        self._exit_stack: AsyncExitStack | None = None

    def add_stdio_server(self, name: str, command: list[str], env: dict = None):
        self._servers[name] = {"type": "stdio", "command": command, "env": env}
        logger.info(f"MCP 注册(stdio): {name}")

    def add_sse_server(self, name: str, url: str):
        self._servers[name] = {"type": "sse", "url": url}
        logger.info(f"MCP 注册(sse): {name} → {url}")

    def discover_all(self) -> int:
        """同步入口：发现所有 MCP 服务器的工具"""
        return self.bridge.run_coro(self._discover_all())

    async def _discover_all(self) -> int:
        """进入所有连接 → 发现工具 → 保持连接"""
        self._exit_stack = AsyncExitStack()
        total = 0
        for name, config in self._servers.items():
            try:
                count = await self._connect(name, config)
                total += count
            except Exception as e:
                logger.error(f"MCP 连接失败 [{name}]: {e}")
        logger.info(f"MCP 发现完成: {total} 个工具（连接持久化）")
        return total

    async def _connect(self, name: str, config: dict) -> int:
        """连接一个 MCP Server（用 exit_stack 保持连接不关闭）"""
        if config["type"] == "stdio":
            cmd = config["command"]
            params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=config.get("env"))
            transport_ctx = stdio_client(params)
        else:
            transport_ctx = sse_client(config["url"])

        # 用 exit_stack 管理生命周期 — 连接不会在发现后关闭
        read, write = await self._exit_stack.enter_async_context(transport_ctx)
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))

        await session.initialize()
        logger.info(f"MCP 已连接: {name}")

        result = await session.list_tools()
        tools = result.tools if hasattr(result, "tools") else result.get("tools", [])

        for tool_def in tools:
            td = tool_def if isinstance(tool_def, dict) else tool_def.model_dump()
            wrapper = MCPToolWrapper(td, session, self.bridge)
            t = wrapper.to_tool()
            self._discovered_tools.append(t)
            logger.info(f"  MCP 工具: {t.name}")

        return len(tools)

    def register_to(self, registry: ToolRegistry) -> int:
        count = 0
        for tool in self._discovered_tools:
            if not registry.get(tool.name):
                registry.register(tool)
                count += 1
        if count:
            logger.info(f"MCP 工具已注入: {count} 个")
        return count

    @property
    def discovered_count(self) -> int:
        return len(self._discovered_tools)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self._discovered_tools]


# ============================================
# 4. 快捷函数
# ============================================

def discover_mcp_tools_stdio(
    registry: ToolRegistry,
    server_name: str,
    command: list[str],
    env: dict = None,
) -> int:
    """连接一个 stdio MCP Server 并注入工具（同步）"""
    manager = MCPClientManager()
    manager.add_stdio_server(server_name, command, env)
    count = manager.discover_all()
    manager.register_to(registry)
    return count