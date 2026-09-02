"""返给前端的溯源条目（摘录，不含整块正文；）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatSource:
    index: int
    documentId: str
    documentTitle: str
    heading: str | None
    excerpt: str
    score: float
