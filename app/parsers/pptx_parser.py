"""PPTX → Markdown 解析（优先 OOXML/ZIP 自研路径）。

按 slideN.xml 顺序输出 `## 幻灯片 N` + 表格 + 标题 + 正文；
表格提取后从 XML 中剔除，避免单元格文本重复出现在正文。
"""
from __future__ import annotations

import io
import re
import zipfile

from .markdown_util import clean_markdown, to_markdown_table


def parse_pptx(buffer: bytes) -> str:
    try:
        return _parse_pptx_with_zip(buffer)
    except Exception:
        return _parse_pptx_fallback(buffer)


def _parse_pptx_fallback(buffer: bytes) -> str:
    """降级路径：无 officeparser 等价物，退回仅提取各页文本的简化实现。"""
    try:
        return _parse_pptx_with_zip(buffer)
    except Exception:
        raise


def _parse_pptx_with_zip(buffer: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(buffer)) as zf:
        slide_paths = [
            name
            for name in zf.namelist()
            if re.match(r"^ppt/slides/slide\d+\.xml$", name, re.IGNORECASE)
        ]
        slide_paths.sort(key=_slide_number)
        if not slide_paths:
            raise ValueError("未找到 PPTX slide")

        parts: list[str] = []
        for i, path in enumerate(slide_paths):
            xml = zf.read(path).decode("utf-8", errors="replace")
            parts.append(f"## 幻灯片 {i + 1}\n")

            tables = _extract_tables(xml)
            for table in tables:
                parts.append(to_markdown_table(table))

            body_xml = re.sub(r"<a:tbl[\s\S]*?</a:tbl>", "", xml)

            title_texts = _extract_placeholder_texts(body_xml, re.compile(r"ctrTitle|title", re.IGNORECASE))
            body_paragraphs = _extract_paragraph_texts(body_xml)

            used = {t.strip() for t in title_texts if t.strip()}
            for t in title_texts:
                text = t.strip()
                if text:
                    parts.append(f"### {text}\n")

            body_lines: list[str] = []
            for t in body_paragraphs:
                text = t.strip()
                if not text or text in used:
                    continue
                body_lines.append(text)
            if body_lines:
                parts.append(f"{chr(10).join(body_lines)}\n\n")

        result = clean_markdown("\n".join(parts))
        if not result:
            raise ValueError("PPTX 提取结果为空")
        return result


def _slide_number(path: str) -> int:
    m = re.search(r"slide(\d+)\.xml$", path, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _extract_paragraph_texts(xml: str) -> list[str]:
    paragraphs: list[str] = []
    p_blocks = re.findall(r"<a:p[\s>][\s\S]*?</a:p>", xml) or []
    for p in p_blocks:
        text = _extract_runs_text(p)
        if text.strip():
            paragraphs.append(text)
    return paragraphs


def _extract_placeholder_texts(xml: str, type_pattern: re.Pattern) -> list[str]:
    texts: list[str] = []
    # 注意用 "<p:sp>"（含 >）切分，避免把 <p:spPr/> 误判为新 shape
    shapes = xml.split("<p:sp>")[1:]
    for shape in shapes:
        m = re.search(r'<p:ph[^>]*\btype="([^"]+)"', shape, re.IGNORECASE)
        if not m or not type_pattern.search(m.group(1)):
            continue
        paras = _extract_paragraph_texts(shape)
        joined = "\n".join(paras).strip()
        if joined:
            texts.append(joined)
    return texts


def _extract_tables(xml: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    table_blocks = re.findall(r"<a:tbl[\s\S]*?</a:tbl>", xml) or []
    for block in table_blocks:
        rows: list[list[str]] = []
        tr_blocks = re.findall(r"<a:tr[\s\S]*?</a:tr>", block) or []
        for tr in tr_blocks:
            cells: list[str] = []
            tc_blocks = re.findall(r"<a:tc[\s\S]*?</a:tc>", tr) or []
            for tc in tc_blocks:
                cell_paras = _extract_paragraph_texts(tc)
                cells.append(" ".join(cell_paras).strip())
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _extract_runs_text(xml: str) -> str:
    result = ""
    re_combined = re.compile(r"<a:br\s*/>|<a:t(?:\s[^>]*)?>([\s\S]*?)</a:t>")
    for m in re_combined.finditer(xml):
        if m.group(0).startswith("<a:br"):
            result += "\n"
        else:
            result += _decode_xml(m.group(1))
    return result


def _decode_xml(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
