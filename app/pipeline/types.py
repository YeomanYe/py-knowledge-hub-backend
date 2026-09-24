"""管线共用类型。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocumentChunk:
    chunkId: str
    documentId: str
    documentTitle: str
    content: str
    heading: str | None = None
    chunkIndex: int = 0
    totalChunks: int = 0
    categoryId: str | None = None
    authorId: str | None = None
    teamId: str | None = None
    isPublic: bool | None = None
    docStatus: int | None = None
    publishTime: str | None = None
    embedding: list[float] | None = None


@dataclass
class ChunkHit:
    chunkId: str
    documentId: str
    documentTitle: str
    content: str
    heading: str | None
    score: float
    bm25Score: float | None = None
    vectorScore: float | None = None


@dataclass
class ExtractedEntity:
    name: str
    type: str
    description: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class ExtractedRelation:
    source: str
    target: str
    relation: str
    weight: float | None = None


@dataclass
class ExtractionResult:
    chunkId: str | None = None
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)


@dataclass
class PipelineDocument:
    id: str
    title: str
    content: str
    summary: str | None = None
    categoryId: str | None = None
    authorId: str | None = None
    teamId: str | None = None
    status: int = 0
    tags: str | None = None
    isPublic: bool | None = None
    viewCount: int | None = None
    likeCount: int | None = None
    commentCount: int | None = None
    publishTime: object | None = None
    createdAt: object | None = None
    updatedAt: object | None = None
