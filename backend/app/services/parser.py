"""文档解析与分块:PDF / Markdown / 纯文本。

分块结果会作为"证据定位的最小单位"存入 chunks 表,
locator 记录页码或章节标题,便于前端展示"这段证据来自哪里"。
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .. import config

logger = logging.getLogger(__name__)


@dataclass
class ParsedChunk:
    content: str
    locator: str


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".md", ".markdown"}:
        return "markdown"
    return "txt"


def split_text(text: str, max_chars: int = None) -> List[str]:
    """按段落累积,超长再按句子二次切分。"""
    max_chars = max_chars or config.CHUNK_MAX_CHARS
    pieces: List[str] = []
    buffer = ""

    def flush():
        nonlocal buffer
        if buffer.strip():
            pieces.append(buffer.strip())
        buffer = ""

    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(buffer) + len(paragraph) + 1 <= max_chars:
            buffer = f"{buffer}\n{paragraph}".strip()
        else:
            flush()
            if len(paragraph) <= max_chars:
                buffer = paragraph
            else:
                # 超长段落按句号/分号切
                sub = ""
                for sentence in re.split(r"(?<=[。;.!?！？])", paragraph):
                    if len(sub) + len(sentence) > max_chars and sub:
                        pieces.append(sub.strip())
                        sub = sentence
                    else:
                        sub += sentence
                buffer = sub
    flush()
    return [p for p in pieces if p]


def _parse_markdown(text: str) -> List[ParsedChunk]:
    chunks: List[ParsedChunk] = []
    title = ""
    buffer: List[str] = []

    def flush():
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        for piece in split_text(body):
            chunks.append(ParsedChunk(content=piece, locator=title or "开头"))
        buffer.clear()

    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line):
            flush()
            title = line.lstrip("#").strip()
        else:
            buffer.append(line)
    flush()
    return chunks


def _parse_pdf(path: Path) -> List[ParsedChunk]:
    try:
        try:
            import pymupdf as fitz  # PyMuPDF 1.24+ 推荐包名
        except ImportError:
            import fitz
    except ImportError:
        raise RuntimeError("未安装 PyMuPDF,无法解析 PDF:pip install pymupdf")

    chunks: List[ParsedChunk] = []
    doc = fitz.open(path)
    try:
        for page_no in range(doc.page_count):
            text = doc[page_no].get_text("text") or ""
            for piece in split_text(text):
                chunks.append(ParsedChunk(content=piece, locator=f"第 {page_no + 1} 页"))
    finally:
        doc.close()
    return chunks


def parse_file(path: Path, file_type: str) -> List[ParsedChunk]:
    if file_type == "pdf":
        return _parse_pdf(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    if file_type == "markdown":
        return _parse_markdown(text)
    return [ParsedChunk(content=p, locator="") for p in split_text(text)]
