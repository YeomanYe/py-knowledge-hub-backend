"""RAG 向量索引（Elasticsearch kh_chunk，dense_vector + IK）。

检索按当前用户可见范围过滤（公开 ∪ 自己写的 ∪ 所在团队）。
"""
from __future__ import annotations

import datetime
import logging

from elasticsearch import AsyncElasticsearch, NotFoundError

from ..config import get_settings
from ..document_access import (
    ES_CHUNK_VISIBILITY_FIELDS,
    DocumentAccessScope,
    es_visibility_filter,
    wrap_es_query,
)
from .types import ChunkHit, DocumentChunk

CHUNK_INDEX = "kh_chunk"

logger = logging.getLogger("vector-index")


class VectorIndexService:
    def __init__(self) -> None:
        settings = get_settings()
        self._es_enabled = settings.elasticsearch_enabled
        self._embedding_dims = settings.embedding_dimension
        self._node = settings.elasticsearch_node
        self.es: AsyncElasticsearch | None = None
        if self._es_enabled:
            self.es = AsyncElasticsearch([self._node])

    async def close(self) -> None:
        if self.es is not None:
            await self.es.close()
            self.es = None

    async def delete_by_doc_id(self, document_id: str) -> None:
        if self.es is None:
            logger.warning("跳过删除向量块（ES 不可用）：documentId=%s", document_id)
            return
        try:
            await self.es.delete_by_query(
                index=CHUNK_INDEX,
                query={"term": {"document_id": document_id}},
                refresh=True,
            )
            logger.info("已从 ES 删除文档向量块：documentId=%s", document_id)
        except NotFoundError:
            return
        except Exception as err:
            logger.error("ES 删除文档块失败：documentId=%s, error=%s", document_id, err)

    async def index_chunks(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        if self.es is None:
            logger.warning("跳过向量索引写入（ES 不可用）：chunks=%s", len(chunks))
            return

        await self._create_index_if_not_exists()
        await self._ensure_visibility_mapping()

        operations: list[dict] = []
        for chunk in chunks:
            operations.append({"index": {"_index": CHUNK_INDEX, "_id": chunk.chunkId}})
            operations.append(self._build_doc_map(chunk))

        response = await self.es.bulk(operations=operations, refresh=True)

        if response.errors:
            failed = []
            for item in response["items"]:
                if item.get("index", {}).get("error"):
                    failed.append(
                        f"{item['index'].get('_id')}: {item['index']['error'].get('reason', 'unknown')}"
                    )
            logger.error("ES 批量索引部分失败：%s", ", ".join(failed))
            raise RuntimeError(f"ES 批量索引部分失败：{len(failed)} 条")

        logger.info("ES 批量索引成功：%s chunks → %s", len(chunks), CHUNK_INDEX)

    async def update_visibility(
        self,
        document_id: str,
        vis: dict,
    ) -> None:
        """已发布文档只改公开/团队时，批量改 chunk 可见性，不必重算向量。"""
        if self.es is None:
            logger.warning("跳过向量可见性更新（ES 不可用）：documentId=%s", document_id)
            return
        try:
            await self.es.update_by_query(
                index=CHUNK_INDEX,
                refresh=True,
                query={"term": {"document_id": document_id}},
                script={
                    "source": (
                        "ctx._source.is_public = params.is_public; "
                        "ctx._source.team_id = params.team_id; "
                        "ctx._source.author_id = params.author_id;"
                    ),
                    "params": {
                        "is_public": vis.get("isPublic", False),
                        "team_id": vis.get("teamId"),
                        "author_id": vis.get("authorId"),
                    },
                },
            )
            logger.info("向量索引可见性已更新：documentId=%s", document_id)
        except Exception as err:
            logger.warning("向量索引可见性更新失败：documentId=%s, %s", document_id, err)

    async def keyword_search(
        self, query: str, top_k: int = 20, scope: DocumentAccessScope | None = None
    ) -> list[ChunkHit]:
        if self.es is None:
            logger.warning("跳过关键词检索（ES 不可用）")
            return []
        trimmed = query.strip()
        if not trimmed:
            return []
        k = self._clamp_top_k(top_k)
        vis = es_visibility_filter(scope, ES_CHUNK_VISIBILITY_FIELDS) if scope else None
        try:
            response = await self.es.search(
                index=CHUNK_INDEX,
                size=k,
                query=wrap_es_query(
                    {
                        "multi_match": {
                            "query": trimmed,
                            "fields": ["document_title^2", "content"],
                            "analyzer": "ik_smart",
                        }
                    },
                    vis,
                ),
                _source=["chunk_id", "document_id", "document_title", "content", "heading"],
            )
            return self._map_hits(response["hits"]["hits"])
        except Exception as err:
            logger.warning("关键词检索失败：%s", err)
            return []

    async def knn_search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        scope: DocumentAccessScope | None = None,
    ) -> list[ChunkHit]:
        if self.es is None:
            logger.warning("跳过向量检索（ES 不可用）")
            return []
        if not query_vector:
            return []
        k = self._clamp_top_k(top_k)
        vis = es_visibility_filter(scope, ES_CHUNK_VISIBILITY_FIELDS) if scope else None
        try:
            knn: dict = {
                "field": "embedding",
                "query_vector": query_vector,
                "k": k,
                "num_candidates": max(k * 10, 50),
            }
            if vis:
                knn["filter"] = vis
            response = await self.es.search(
                index=CHUNK_INDEX,
                size=k,
                knn=knn,
                _source=["chunk_id", "document_id", "document_title", "content", "heading"],
            )
            return self._map_hits(response["hits"]["hits"])
        except Exception as err:
            logger.warning("向量检索失败：%s", err)
            return []

    async def search_hybrid(
        self,
        query: str,
        query_vector: list[float] | None = None,
        hybrid_top_k: int = 20,
        rrf_c: int = 60,
        scope: DocumentAccessScope | None = None,
    ) -> list[ChunkHit]:
        hybrid_top_k = self._clamp_top_k(hybrid_top_k)
        rrf_c = rrf_c if rrf_c and rrf_c > 0 else 60

        keyword_task = self.keyword_search(query, hybrid_top_k, scope)
        vector_task = (
            self.knn_search(query_vector, hybrid_top_k, scope)
            if query_vector
            else None
        )
        keyword_hits = await keyword_task
        vector_hits = await vector_task if vector_task is not None else []

        fused = self._rrf_fuse(keyword_hits, vector_hits, rrf_c)
        logger.info(
            "混合检索 RRF：keyword=%s, vector=%s, fused=%s",
            len(keyword_hits), len(vector_hits), len(fused),
        )
        return fused

    def _clamp_top_k(self, top_k: int) -> int:
        return max(1, min(top_k, 50))

    def _map_hits(self, hits: list[dict]) -> list[ChunkHit]:
        result: list[ChunkHit] = []
        for hit in hits:
            src = hit.get("_source") or {}
            result.append(
                ChunkHit(
                    chunkId=str(src.get("chunk_id") or hit.get("_id") or ""),
                    documentId=str(src.get("document_id") or ""),
                    documentTitle=str(src.get("document_title") or ""),
                    content=str(src.get("content") or ""),
                    heading=src.get("heading"),
                    score=float(hit.get("_score") or 0),
                )
            )
        return result

    def _rrf_fuse(
        self, keyword_hits: list[ChunkHit], vector_hits: list[ChunkHit], rrf_c: int
    ) -> list[ChunkHit]:
        fused: dict[str, ChunkHit] = {}

        def add_channel(hits: list[ChunkHit], channel: str) -> None:
            sorted_hits = sorted(hits, key=lambda h: h.score, reverse=True)
            for rank, hit in enumerate(sorted_hits):
                rrf = 1 / (rrf_c + rank + 1)
                existing = fused.get(hit.chunkId)
                if existing is None:
                    fused[hit.chunkId] = ChunkHit(
                        chunkId=hit.chunkId,
                        documentId=hit.documentId,
                        documentTitle=hit.documentTitle,
                        content=hit.content,
                        heading=hit.heading,
                        score=rrf,
                        bm25Score=hit.score if channel == "keyword" else 0,
                        vectorScore=hit.score if channel == "vector" else 0,
                    )
                    continue
                existing.score += rrf
                if channel == "keyword":
                    existing.bm25Score = hit.score
                if channel == "vector":
                    existing.vectorScore = hit.score

        add_channel(keyword_hits, "keyword")
        add_channel(vector_hits, "vector")

        return sorted(fused.values(), key=lambda h: h.score, reverse=True)

    async def _create_index_if_not_exists(self) -> None:
        if self.es is None:
            return
        exists = await self.es.indices.exists(index=CHUNK_INDEX)
        if exists:
            return
        try:
            await self.es.indices.create(
                index=CHUNK_INDEX,
                settings={
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "refresh_interval": "5s",
                },
                mappings={
                    "properties": {
                        "chunk_id": {"type": "keyword"},
                        "document_id": {"type": "keyword"},
                        "document_title": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                            "fields": {"keyword": {"type": "keyword"}},
                        },
                        "content": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "heading": {"type": "keyword"},
                        "chunk_index": {"type": "integer"},
                        "total_chunks": {"type": "integer"},
                        "category_id": {"type": "keyword"},
                        "author_id": {"type": "keyword"},
                        "team_id": {"type": "keyword"},
                        "is_public": {"type": "boolean"},
                        "doc_status": {"type": "integer"},
                        "publish_time": {"type": "date"},
                        "indexed_at": {"type": "date"},
                        "embedding": {
                            "type": "dense_vector",
                            "dims": self._embedding_dims,
                            "index": True,
                            "similarity": "cosine",
                        },
                    }
                },
            )
            logger.info("ES 索引创建成功：index=%s, dims=%s", CHUNK_INDEX, self._embedding_dims)
        except Exception as err:
            if "resource_already_exists" in str(err):
                return
            logger.error("ES 索引创建失败：%s", err)
            raise

    async def _ensure_visibility_mapping(self) -> None:
        if self.es is None:
            return
        try:
            await self.es.indices.put_mapping(
                index=CHUNK_INDEX,
                properties={
                    "is_public": {"type": "boolean"},
                    "author_id": {"type": "keyword"},
                    "team_id": {"type": "keyword"},
                },
            )
        except Exception as err:
            logger.warning("kh_chunk 可见性 mapping 更新失败：%s", err)

    def _build_doc_map(self, chunk: DocumentChunk) -> dict:
        doc: dict = {
            "chunk_id": chunk.chunkId,
            "document_id": chunk.documentId,
            "document_title": chunk.documentTitle,
            "content": chunk.content,
            "heading": chunk.heading,
            "chunk_index": chunk.chunkIndex,
            "total_chunks": chunk.totalChunks,
            "category_id": chunk.categoryId,
            "author_id": chunk.authorId,
            "team_id": chunk.teamId,
            "is_public": chunk.isPublic or False,
            "doc_status": chunk.docStatus,
            "publish_time": chunk.publishTime,
            "indexed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if chunk.embedding:
            doc["embedding"] = chunk.embedding
        return doc
