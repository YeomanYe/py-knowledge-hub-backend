"""发布后知识管线编排器。

RAG：分块 → Embedding → ES kh_chunk
Search：Mongo 全文 → ES kh_document
KG：分块 → 抽实体关系 → Neo4j
"""
from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Document, DocumentStatus
from ..mongo_models import DocumentContentRepo
from .chunking import ChunkingService
from .embedding import EmbeddingService
from .graph_build import GraphBuildService
from .search_index import SearchIndexService
from .types import PipelineDocument
from .vector_index import VectorIndexService

logger = logging.getLogger("pipeline-orchestrator")


def _to_iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    try:
        return datetime.datetime.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


class PipelineOrchestrator:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        content_repo: DocumentContentRepo,
        chunking_service: ChunkingService,
        embedding_service: EmbeddingService,
        vector_index_service: VectorIndexService,
        search_index_service: SearchIndexService,
        graph_build_service: GraphBuildService,
    ) -> None:
        self._session_factory = session_factory
        self._content_repo = content_repo
        self._chunking_service = chunking_service
        self._embedding_service = embedding_service
        self._vector_index_service = vector_index_service
        self._search_index_service = search_index_service
        self._graph_build_service = graph_build_service

    # ------------------------------------------------------------ RAG
    async def handle_rag_reindex(self, type_: str, document_ids: list[str] | None = None) -> None:
        if type_ == "DELETE_BY_DOC_IDS" and document_ids:
            for doc_id in document_ids:
                await self._vector_index_service.delete_by_doc_id(doc_id)
            return

        if type_ != "BY_DOC_IDS" or not document_ids:
            logger.warning("忽略未支持的 RAG 消息：type=%s", type_)
            return

        docs = await self._load_documents_by_ids(document_ids)
        logger.info("RAG 开始索引：type=%s, total=%s", type_, len(docs))

        for doc in docs:
            try:
                await self._reindex_one(doc)
            except Exception as err:
                logger.error("RAG 索引失败：documentId=%s, %s", doc.id, err)

    # ------------------------------------------------------------ Search
    async def handle_search_index(self, type_: str, document_id: str) -> None:
        if type_ == "DELETE":
            await self._search_index_service.delete_document(document_id)
            return

        if type_ == "INDEX":
            docs = await self._load_documents_by_ids([document_id])
            doc = docs[0] if docs else None
            if not doc:
                logger.warning("Search INDEX 文档不存在：documentId=%s", document_id)
                return
            await self._search_index_service.index_document(self._to_search_index_doc(doc))
            return

        logger.warning("忽略未支持的 Search 消息：type=%s", type_)

    # ------------------------------------------------------------ KG
    async def handle_kg_build(self, type_: str, document_ids: list[str] | None = None) -> None:
        if type_ == "DELETE_BY_DOC_IDS" and document_ids:
            for doc_id in document_ids:
                await self._graph_build_service.delete_for_document(doc_id)
            return

        if type_ == "BUILD_BY_DOC_IDS" and document_ids:
            docs = await self._load_documents_by_ids(document_ids)
        elif type_ == "BUILD_ALL":
            docs = await self._load_all_published_documents()
        else:
            docs = []

        if not docs:
            logger.warning("忽略未支持或空的 KG 消息：type=%s", type_)
            return

        logger.info("KG 开始建图：type=%s, total=%s", type_, len(docs))
        await self._graph_build_service.build_batch(docs)

    # ------------------------------------------------------------ 内部
    async def _reindex_one(self, doc: PipelineDocument) -> None:
        if not (doc.content or "").strip():
            logger.warning("文档内容为空，跳过 RAG：documentId=%s", doc.id)
            return

        await self._vector_index_service.delete_by_doc_id(doc.id)

        chunks = await self._chunking_service.chunk(
            content=doc.content,
            document_id=doc.id,
            document_title=doc.title,
            category_id=doc.categoryId,
            author_id=doc.authorId,
            team_id=doc.teamId,
            doc_status=doc.status,
            publish_time=_to_iso(doc.publishTime),
        )
        if not chunks:
            return

        embeddings = await self._embedding_service.embed_batch([c.content for c in chunks])
        for i, chunk in enumerate(chunks):
            chunk.embedding = embeddings[i]

        await self._vector_index_service.index_chunks(chunks)
        logger.info("RAG 索引完成：documentId=%s, chunks=%s", doc.id, len(chunks))

    async def _load_documents_by_ids(self, ids: list[str]) -> list[PipelineDocument]:
        result: list[PipelineDocument] = []
        async with self._session_factory() as session:
            for doc_id in ids:
                doc = await self._find_document(session, doc_id)
                if not doc:
                    continue
                content_doc = await self._content_repo.find_by_id(doc.content_id)
                result.append(self._to_pipeline_doc(doc, (content_doc or {}).get("content", "")))
        return result

    async def _load_all_published_documents(self) -> list[PipelineDocument]:
        result: list[PipelineDocument] = []
        async with self._session_factory() as session:
            stmt = select(Document).where(
                Document.deleted.is_(False), Document.status == DocumentStatus.Published
            )
            docs = (await session.execute(stmt)).scalars().all()
            for doc in docs:
                content_doc = await self._content_repo.find_by_id(doc.content_id)
                result.append(self._to_pipeline_doc(doc, (content_doc or {}).get("content", "")))
        return result

    async def _find_document(self, session: AsyncSession, doc_id: str) -> Document | None:
        stmt = select(Document).where(Document.id == doc_id, Document.deleted.is_(False))
        return (await session.execute(stmt)).scalars().first()

    def _to_search_index_doc(self, doc: PipelineDocument) -> dict:
        return {
            "id": doc.id,
            "title": doc.title,
            "summary": doc.summary,
            "content": doc.content or "",
            "categoryId": doc.categoryId,
            "tags": doc.tags,
            "status": doc.status,
            "isPublic": doc.isPublic,
            "viewCount": doc.viewCount,
            "likeCount": doc.likeCount,
            "commentCount": doc.commentCount,
            "authorId": doc.authorId,
            "publishTime": _to_iso(doc.publishTime),
            "createdAt": _to_iso(doc.createdAt),
            "updatedAt": _to_iso(doc.updatedAt),
        }

    def _to_pipeline_doc(self, doc: Document, content: str) -> PipelineDocument:
        return PipelineDocument(
            id=doc.id,
            title=doc.title,
            content=content,
            summary=doc.summary,
            categoryId=doc.category_id,
            authorId=doc.author_id,
            teamId=doc.team_id,
            status=doc.status,
            tags=doc.tags,
            isPublic=doc.is_public,
            viewCount=doc.view_count,
            likeCount=doc.like_count,
            commentCount=doc.comment_count,
            publishTime=doc.publish_time,
            createdAt=doc.created_at,
            updatedAt=doc.updated_at,
        )
