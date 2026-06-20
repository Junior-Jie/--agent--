# ============================================
# 2026-06-19 - 文档加载与切分模块
# 职责：读取文件 → 切成小块 → 返回文本块列表
#
# 防护机制：
#   - 单文件大小上限（防止加载超大文件撑爆内存）
#   - 总块数上限（防止知识库无限膨胀）
#   - 逐文件加载（不全部读进内存再处理）
# ============================================

import os
from pathlib import Path
from loguru import logger

from langchain_text_splitters import RecursiveCharacterTextSplitter


class DocumentLoader:
    """文档加载器（带内存/磁盘防护）"""

    SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown"}

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        # ===== 防护参数 =====
        max_file_size_mb: int = 250,         # 单文件最大 250MB，超过跳过
        max_chunks_per_file: int = 200,      # 单文件最多切 200 块
        max_total_chunks: int = 10000,       # 总知识库最多 10000 块
        # 按每块 500 字算，10000 块 ≈ 500 万字，足够中等企业用
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self.max_chunks_per_file = max_chunks_per_file
        self.max_total_chunks = max_total_chunks

        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        )

        # 统计计数器
        self._skipped_files = 0
        self._truncated_files = 0

        logger.info(
            f"DocumentLoader 初始化 "
            f"(块={chunk_size}, 重叠={chunk_overlap}, "
            f"单文件上限={max_file_size_mb}MB, 单文件最多{max_chunks_per_file}块, "
            f"总计上限={max_total_chunks}块)"
        )

    def load_directory(self, dir_path: str) -> list[dict]:
        """加载目录下所有文档，超限自动跳过/截断"""
        if not os.path.isdir(dir_path):
            logger.error(f"目录不存在: {dir_path}")
            return []

        self._skipped_files = 0
        self._truncated_files = 0
        all_chunks = []

        for root, dirs, files in os.walk(dir_path):
            for filename in files:
                # 每次添加前检查总量上限
                remaining = self.max_total_chunks - len(all_chunks)
                if remaining <= 0:
                    logger.warning(
                        f"已达到知识库总量上限 {self.max_total_chunks} 块，"
                        f"跳过剩余文件（已跳过 {self._skipped_files} 个）"
                    )
                    break

                ext = Path(filename).suffix.lower()
                if ext not in self.SUPPORTED_EXTENSIONS:
                    continue

                filepath = os.path.join(root, filename)

                # 检查文件大小
                file_size = os.path.getsize(filepath)
                if file_size > self.max_file_size_bytes:
                    self._skipped_files += 1
                    logger.warning(
                        f"跳过文件 {filename}：大小 {file_size / 1024 / 1024:.1f}MB "
                        f"超过上限 {self.max_file_size_bytes / 1024 / 1024:.0f}MB"
                    )
                    continue

                # 加载并切块
                chunks = self.load_file(filepath, max_chunks=min(
                    self.max_chunks_per_file, remaining
                ))
                all_chunks.extend(chunks)

            # 内层 for 退出了也要检查
            if len(all_chunks) >= self.max_total_chunks:
                break

        logger.info(
            f"共加载 {len(all_chunks)} 个文本块"
            + (f"，跳过 {self._skipped_files} 个超大文件" if self._skipped_files else "")
            + (f"，截断 {self._truncated_files} 个过长文件" if self._truncated_files else "")
        )

        if len(all_chunks) >= self.max_total_chunks:
            logger.warning(
                f"⚠️ 已达到知识库总量上限 {self.max_total_chunks} 块。"
                f"如需扩容，请修改 max_total_chunks 参数。"
            )

        return all_chunks

    def load_file(self, filepath: str, max_chunks: int = 200) -> list[dict]:
        """加载单个文件并切块"""
        if not os.path.isfile(filepath):
            logger.error(f"文件不存在: {filepath}")
            return []

        # 1. 读取
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(filepath, "r", encoding="gbk") as f:
                content = f.read()

        if not content.strip():
            return []

        # 2. 切块
        chunks_text = self.splitter.split_text(content)

        # 3. 截断
        if len(chunks_text) > max_chunks:
            self._truncated_files += 1
            logger.warning(
                f"文件 {os.path.basename(filepath)} 切出 {len(chunks_text)} 块，"
                f"截断为 {max_chunks} 块"
            )
            chunks_text = chunks_text[:max_chunks]

        # 4. 组装
        filename = os.path.basename(filepath)
        title = self._extract_title(content) or filename

        result = []
        for i, chunk_text in enumerate(chunks_text):
            result.append({
                "content": chunk_text,
                "metadata": {
                    "source": filename,
                    "title": title,
                    "chunk_index": i,
                    "filepath": filepath,
                },
            })

        logger.info(f"文件 {filename} → {len(result)} 个块")
        return result

    def _extract_title(self, content: str) -> str | None:
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return None

    def get_file_stats(self, dir_path: str) -> dict:
        """文档统计（不实际加载内容）"""
        files = []
        total_chars = 0
        total_bytes = 0
        for root, dirs, filenames in os.walk(dir_path):
            for f in filenames:
                ext = Path(f).suffix.lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    filepath = os.path.join(root, f)
                    files.append(f)
                    total_bytes += os.path.getsize(filepath)
                    try:
                        with open(filepath, "r", encoding="utf-8") as fp:
                            total_chars += len(fp.read())
                    except Exception:
                        pass
        return {
            "total_files": len(files),
            "total_chars": total_chars,
            "total_mb": round(total_bytes / 1024 / 1024, 2),
            "file_list": files,
        }