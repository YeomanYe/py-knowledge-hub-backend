"""Markdown 工具函数。"""
from __future__ import annotations

import re


def clean_markdown(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def escape_table_cell(value: str) -> str:
    return re.sub(r"\|", "\\|", value).replace("\r\n", " ").replace("\n", " ").strip()


def to_markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    max_cols = max((len(row) for row in rows), default=0)
    if max_cols == 0:
        return ""

    lines: list[str] = []
    for r, row in enumerate(rows):
        cells = []
        for c in range(max_cols):
            cells.append(escape_table_cell(row[c] if c < len(row) else ""))
        lines.append(f"| {' | '.join(cells)} |")
        if r == 0:
            lines.append(f"| {' | '.join(['---'] * max_cols)} |")
    return f"{chr(10).join(lines)}\n\n"


def get_extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename[filename.rfind(".") + 1 :].lower()


def decode_upload_filename(filename: str | None) -> str:
    """Multer/busboy 常把 UTF-8 字节按 Latin-1 解码，按 Latin-1 还原字节再按 UTF-8 解码。"""
    if not filename:
        return ""
    try:
        decoded = filename.encode("latin-1", errors="replace").decode("utf-8", errors="replace")
        if "\ufffd" in decoded:
            return filename
        return decoded
    except Exception:
        return filename


def title_from_filename(filename: str | None) -> str:
    if not filename:
        return "未命名文档"
    idx = filename.rfind(".")
    return filename[:idx] if idx > 0 else filename
