# ============================================
# 2026-06-19 - 演示 MCP Server
# 职责：模拟外部系统提供工具，验证 MCP Client 的发现和调用能力
#
# 使用方式：
#   作为子进程被 MCP Client 启动：
#     python src/mcp/demo_server.py
#
# 提供的工具：
#   1. get_weather    — 模拟天气查询
#   2. translate_text — 模拟翻译
#   3. get_system_status — 模拟系统监控
# ============================================

import random
from datetime import datetime

from mcp.server import FastMCP

# 创建 FastMCP 服务器
mcp = FastMCP("Demo External Services")


# ============================================
# 工具 1: 天气查询
# ============================================

@mcp.tool()
def get_weather(city: str) -> str:
    """
    查询城市天气（模拟外部天气 API）

    参数:
        city: 城市名称，如 "北京"、"上海"
    """
    conditions = ["晴", "多云", "小雨", "阴", "晴转多云"]
    temp = random.randint(15, 35)
    humidity = random.randint(30, 90)
    condition = random.choice(conditions)

    return (
        f"城市: {city}\n"
        f"天气: {condition}\n"
        f"温度: {temp}℃\n"
        f"湿度: {humidity}%\n"
        f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )


# ============================================
# 工具 2: 翻译
# ============================================

@mcp.tool()
def translate_text(text: str, target_lang: str = "英文") -> str:
    """
    文本翻译（模拟翻译服务）

    参数:
        text:        要翻译的文本
        target_lang: 目标语言，如 "英文"、"日文"、"韩文"
    """
    # 模拟翻译（实际应接翻译 API）
    mock_translations = {
        ("你好", "英文"): "Hello",
        ("谢谢", "英文"): "Thank you",
        ("再见", "英文"): "Goodbye",
        ("你好", "日文"): "こんにちは",
        ("谢谢", "日文"): "ありがとう",
    }

    key = (text, target_lang)
    if key in mock_translations:
        translated = mock_translations[key]
    else:
        translated = f"[{text} → {target_lang} 翻译结果]"

    return f"原文: {text}\n目标语言: {target_lang}\n翻译: {translated}"


# ============================================
# 工具 3: 系统状态
# ============================================

@mcp.tool()
def get_system_status() -> str:
    """
    获取系统运行状态（模拟运维监控接口）

    返回 CPU、内存、磁盘等关键指标
    """
    cpu = random.randint(10, 90)
    mem = random.randint(20, 85)
    disk = random.randint(30, 70)
    uptime_hours = random.randint(1, 720)

    status = "✅ 正常" if cpu < 80 and mem < 90 else "⚠️ 负载偏高"

    return (
        f"系统状态: {status}\n"
        f"CPU 使用率: {cpu}%\n"
        f"内存使用率: {mem}%\n"
        f"磁盘使用率: {disk}%\n"
        f"运行时长: {uptime_hours} 小时\n"
        f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )


# ============================================
# 启动服务器
# ============================================
if __name__ == "__main__":
    # 注意：不能 print 到 stdout！MCP 用 stdout 传 JSON-RPC
    import sys as _sys
    _sys.stderr.write("Demo MCP Server started (stdio)\n")
    _sys.stderr.flush()
    mcp.run(transport="stdio")