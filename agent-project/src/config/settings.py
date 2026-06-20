# ============================================
# 2026-06-19 - 项目配置中心
# 作用：从 .env 文件加载所有配置，其他模块导入这个文件即可获取配置
# 好处：配置和代码分离，换环境只需要改 .env，不需要改代码
# ============================================

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# 1. 手动定位项目根目录（src/config/settings.py → 往上 2 级）
#    Path(__file__)  = src/config/settings.py
#    .parent          = src/config/
#    .parent          = src/
#    .parent          = 项目根目录 (agent-project/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 2. 加载 .env 文件（显式指定路径，避免工作目录不同导致找不到）
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

# 3. HuggingFace 国内镜像（解决模型下载被墙的问题）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# 2. 配置 loguru 日志输出（UTF-8 编码，避免 Windows 终端乱码）
logger.remove()  # 清空默认配置
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="DEBUG",
    colorize=True,
)

# 2. 定义配置类
# 把所有配置项集中到一个类中，其他地方 from config.settings import settings 即可
class Settings:
    """项目全局配置"""

    # ===== DeepSeek API 配置 =====
    # os.getenv("KEY", "默认值") → 先从环境变量读，读不到用默认值
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

    # ===== 数据库配置 =====
    # sqlite:///data/tickets.db → SQLite 文件存储，适合开发
    # 生产环境改 postgresql://user:pass@host/db
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data/tickets.db")

    # ===== 应用 API 密钥（客户端认证） =====
    APP_API_KEY: str = os.getenv("APP_API_KEY", "")

    # ===== 向量数据库配置 =====
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "data/chroma_db")

    # ===== 服务配置 =====
    API_PORT: int = int(os.getenv("API_PORT", "8000"))

    # ===== 日志配置 =====
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG")

    # ===== 项目路径（自动计算） =====
    # os.path.dirname(__file__)  → 当前文件所在目录 = src/config/
    # 往上 .parent 两次 → 项目根目录
    @property
    def PROJECT_ROOT(self) -> str:
        """项目根目录路径"""
        import pathlib
        return str(pathlib.Path(__file__).parent.parent.parent)

    @property
    def DATA_DIR(self) -> str:
        """数据目录路径"""
        return os.path.join(self.PROJECT_ROOT, "data")


# 3. 创建全局单例
# 之后其他模块 import settings 就能直接用，不需要重复创建
settings = Settings()