"""XLSX → Markdown 解析（openpyxl 等价 exceljs）。

每个 Sheet → `## SheetName` + Markdown 表格；单元格值经 cell_to_string 统一。
公式单元格优先取缓存计算结果（data_only），无缓存则取公式文本。
"""
from __future__ import annotations

import datetime
import io

from openpyxl import load_workbook

from .markdown_util import clean_markdown, to_markdown_table


def parse_xlsx(buffer: bytes) -> str:
    # 两次加载：data_only 取公式缓存结果；普通加载取公式/富文本原始结构
    wb = load_workbook(io.BytesIO(buffer), data_only=True, read_only=True)
    wb_raw = load_workbook(io.BytesIO(buffer), data_only=False, read_only=True)

    parts: list[str] = []
    for ws, ws_raw in zip(wb.worksheets, wb_raw.worksheets):
        parts.append(f"## {ws.title}\n")

        rows: list[list[str]] = []
        max_cols = 0
        for row, row_raw in zip(ws.iter_rows(values_only=True), ws_raw.iter_rows(values_only=True)):
            cells: list[str] = []
            last = len(row)
            max_cols = max(max_cols, last)
            for c in range(last):
                raw_value = row_raw[c] if c < len(row_raw) else None
                cells.append(cell_to_string(row[c], raw_value))
            rows.append(cells)

        if max_cols == 0 or not rows:
            parts.append("\n")
            continue

        normalized = []
        for r in rows:
            copy = list(r)
            while len(copy) < max_cols:
                copy.append("")
            normalized.append(copy)

        parts.append(to_markdown_table(normalized))

    wb.close()
    wb_raw.close()
    return clean_markdown("\n".join(parts))


def cell_to_string(value, raw_value=None) -> str:
    if value is None:
        # 公式且无缓存结果：返回空（对齐 exceljs 仅公式无 result 时输出空）
        if isinstance(raw_value, str) and raw_value.startswith("="):
            return ""
        return ""

    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()

    if isinstance(value, dict):
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
    return str(value)
