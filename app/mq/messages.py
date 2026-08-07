"""管线消息体。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReindexMessage:
    taskId: str
    type: str  # 'BY_DOC_IDS' | 'DELETE_BY_DOC_IDS'
    documentIds: list[str] = field(default_factory=list)


@dataclass
class SearchIndexMessage:
    taskId: str
    type: str  # 'INDEX' | 'DELETE'
    documentId: str = ""


@dataclass
class KgBuildMessage:
    taskId: str
    type: str  # 'BUILD_ALL' | 'BUILD_BY_DOC_IDS' | 'DELETE_BY_DOC_IDS'
    documentIds: list[str] = field(default_factory=list)
