"""DOCX → Markdown 解析（mammoth + turndown + GFM）。

Python 侧用 python-docx 遍历文档 body（段落 + 表格），按 Word 内置样式映射
标题层级（同时覆盖英文 Heading N 与中文「标题 N」），表格转 Markdown 表格，
内联图片以 data URI 形式内嵌。
"""
from __future__ import annotations

import base64
import io
import re

from docx import Document
from docx.oxml.ns import qn

from .markdown_util import clean_markdown, to_markdown_table

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# 英文 + 中文 Word 内置标题样式名 → 标题层级
_HEADING_STYLES = {
    "title": 1,
    "subtitle": 2,
    "heading 1": 1,
    "heading 2": 2,
    "heading 3": 3,
    "heading 4": 4,
    "heading 5": 5,
    "heading 6": 6,
    "标题 1": 1,
    "标题 2": 2,
    "标题 3": 3,
    "标题 4": 4,
}


def _render_r_xml(paragraph) -> list[str]:
    parts: list[str] = []
    for child in paragraph._p:
        tag = child.tag
        if tag == qn("w:r"):
            text = _run_text_from_xml(child)
            if text:
                parts.append(_apply_run_marks(child, text))
        elif tag == qn("w:hyperlink"):
            rid = child.get(qn("r:id"))
            target = ""
            if rid:
                rel = paragraph.part.rels.get(rid)
                if rel is not None:
                    target = rel.target_ref
            inner = ""
            for sub in child:
                if sub.tag == qn("w:r"):
                    t = _run_text_from_xml(sub)
                    if t:
                        inner += _apply_run_marks(sub, t)
            if target and inner:
                parts.append(f"[{inner}]({target})")
            elif inner:
                parts.append(inner)
        elif tag == qn("w:drawing"):
            data_uri = _drawing_data_uri(paragraph.part, child)
            if data_uri:
                parts.append(data_uri)
    return parts


def _run_text_from_xml(r_element) -> str:
    text = ""
    for node in r_element.iter():
        if node.tag == qn("w:t"):
            text += (node.text or "")
        elif node.tag == qn("w:tab"):
            text += " "
        elif node.tag == qn("w:br"):
            text += "\n"
    return text


def _apply_run_marks(r_element, text: str) -> str:
    bold = r_element.find(qn("w:rPr") + "/" + qn("w:b")) is not None
    italic = r_element.find(qn("w:rPr") + "/" + qn("w:i")) is not None
    if bold:
        text = f"**{text}**"
    if italic:
        text = f"*{text}*"
    return text


def _drawing_data_uri(part, drawing) -> str | None:
    blips = drawing.findall(f".//{{{_A}}}blip")
    for blip in blips:
        rid = blip.get(f"{{{_R}}}embed")
        if not rid:
            continue
        try:
            image_part = part.related_parts[rid]
        except KeyError:
            continue
        blob = image_part.blob
        content_type = getattr(image_part, "content_type", "image/png") or "image/png"
        if content_type in ("image/jpeg", "image/jpg"):
            mime = "image/jpeg"
        elif content_type == "image/gif":
            mime = "image/gif"
        elif content_type == "image/webp":
            mime = "image/webp"
        else:
            mime = "image/png"
        b64 = base64.b64encode(blob).decode("ascii")
        return f"![image](data:{mime};base64,{b64})"
    return None


def _style_heading_level(paragraph) -> int | None:
    style_name = (paragraph.style.name or "").strip()
    lower = style_name.lower()
    if lower in _HEADING_STYLES:
        return _HEADING_STYLES[lower]
    return None


def _paragraph_to_markdown(paragraph) -> str | None:
    text = "".join(_render_r_xml(paragraph)).strip()
    level = _style_heading_level(paragraph)

    if level is not None:
        return f"{'#' * level} {text}" if text else None

    style_name = (paragraph.style.name or "").lower()
    if "code" in style_name or "源码" in style_name or "code block" in style_name:
        return f"```\n{text}\n```" if text else None

    if text:
        return text
    return None


def parse_docx(buffer: bytes) -> str:
    document = Document(io.BytesIO(buffer))
    parts: list[str] = []
    last_was_blank = False

    # 按 body 子节点顺序遍历（段落与表格交错出现时顺序正确）
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            paragraph = _paragraph_from_element(document, child)
            md = _paragraph_to_markdown(paragraph)
            if md:
                if last_was_blank:
                    parts.append("")
                parts.append(md)
                last_was_blank = False
            else:
                parts.append("")
                last_was_blank = True
        elif tag == qn("w:tbl"):
            rows: list[list[str]] = []
            for tr in child.findall(qn("w:tr")):
                cells: list[str] = []
                for tc in tr.findall(qn("w:tc")):
                    cell_text: list[str] = []
                    for p in tc.findall(qn("w:p")):
                        paragraph = _paragraph_from_element(document, p)
                        t = "".join(_render_r_xml(paragraph)).strip()
                        if t:
                            cell_text.append(t)
                    cells.append(" ".join(cell_text).strip())
                if cells:
                    rows.append(cells)
            if rows:
                if last_was_blank:
                    parts.append("")
                parts.append(to_markdown_table(rows))
                last_was_blank = False

    return clean_markdown("\n".join(parts))


def _paragraph_from_element(document, p_element):
    """从 w:p XML 元素构造 python-docx Paragraph（用于直接渲染）。"""
    from docx.text.paragraph import Paragraph

    return Paragraph(p_element, document)
