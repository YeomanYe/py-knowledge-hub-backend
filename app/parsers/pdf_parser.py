"""PDF → Markdown 解析（pdf-parse 等价 pypdf + pdfplumber）。

- 按页提取文本（pypdf）；
- 可选 upload_image 回调：提取图片 → 过滤小图（阈值默认 50）→ 上传 → 按页 `![](url)`；
- 表格用 pdfplumber 尽力提取，正文无 Markdown 表头时追加「检测到的表格」章节；
- 图片提取失败不中断解析（降级仅文本）。
"""
from __future__ import annotations

import io
import logging
from typing import Awaitable, Callable

from pypdf import PdfReader

from .markdown_util import clean_markdown, to_markdown_table

logger = logging.getLogger("pdf-parser")

ImageUploader = Callable[[bytes, str, str], Awaitable[str]]


async def parse_pdf(
    buffer: bytes,
    upload_image: ImageUploader | None = None,
    image_threshold: int = 50,
) -> str:
    threshold = image_threshold if image_threshold and image_threshold > 0 else 50
    reader = PdfReader(io.BytesIO(buffer))

    # ---------- 1. 文本 + 2. 图片 ----------
    page_texts: list[tuple[int, str]] = []
    page_image_urls: dict[int, list[str]] = {}

    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        page_texts.append((idx, text))

        if upload_image is not None:
            try:
                urls: list[str] = []
                img_idx = 0
                for image in page.images:
                    width = getattr(image, "width", 0) or 0
                    height = getattr(image, "height", 0) or 0
                    if (width > 0 and width < threshold) or (height > 0 and height < threshold):
                        continue
                    data = getattr(image, "data", None)
                    if not data:
                        continue
                    content_type = _sniff_image_content_type(bytes(data))
                    ext = "jpg" if content_type == "image/jpeg" else "png"
                    file_name = f"pdf_img_p{idx}_{img_idx}.{ext}"
                    img_idx += 1
                    try:
                        url = await upload_image(bytes(data), file_name, content_type)
                        urls.append(url)
                    except Exception as err:
                        logger.warning(
                            "PDF 图片上传失败: page=%s, name=%s, err=%s", idx, file_name, err
                        )
                if urls:
                    page_image_urls[idx] = urls
            except Exception as err:
                logger.warning("PDF 图片提取失败，继续仅文本: %s", err)

    # ---------- 3. 按页拼装 ----------
    parts: list[str] = []
    for page_num, text in page_texts:
        if text:
            parts.append(text)
        for url in page_image_urls.get(page_num, []):
            parts.append(f"![]({url})")
        if text or page_image_urls.get(page_num):
            parts.append("")

    markdown = clean_markdown("\n\n".join(parts))

    # ---------- 4. 表格（尽力而为）----------
    try:
        table_parts: list[str] = []
        table_idx = 0
        with _pdfplumber_open(buffer) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    rows = _normalize_pdf_table(table)
                    if rows:
                        table_idx += 1
                        table_parts.append(f"### 表格 {table_idx}\n\n{to_markdown_table(rows)}")
        if table_parts and "| ---" not in markdown:
            markdown = clean_markdown(f"{markdown}\n\n## 检测到的表格\n\n{''.join(table_parts)}")
    except Exception:
        pass

    return markdown


def _pdfplumber_open(buffer: bytes):
    import pdfplumber

    return pdfplumber.open(io.BytesIO(buffer))


def _sniff_image_content_type(data: bytes) -> str:
    if len(data) >= 3 and data[0] == 0xFF and data[1] == 0xD8 and data[2] == 0xFF:
        return "image/jpeg"
    if len(data) >= 8 and data[0] == 0x89 and data[1] == 0x50 and data[2] == 0x4E and data[3] == 0x47:
        return "image/png"
    if len(data) >= 4 and data[0] == 0x52 and data[1] == 0x49 and data[2] == 0x46 and data[3] == 0x46:
        return "image/webp"
    return "image/png"


def _normalize_pdf_table(raw) -> list[list[str]]:
    if not raw:
        return []

    if isinstance(raw, list):
        if len(raw) == 0:
            return []
        if isinstance(raw[0], list):
            rows: list[list[str]] = []
            for row in raw:
                rows.append([str(cell if cell is not None else "").strip() for cell in row])
            return rows
        merged: list[list[str]] = []
        for item in raw:
            merged.extend(_normalize_pdf_table(item))
        return merged

    if isinstance(raw, dict):
        if raw.get("rows"):
            return _normalize_pdf_table(raw["rows"])
        if raw.get("data"):
            return _normalize_pdf_table(raw["data"])

    return []
