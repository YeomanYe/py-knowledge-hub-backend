"""kh_chunk 混合检索。

向量召回 + 关键词召回 → RRF 粗融合 → reranker 精排。
"""
from __future__ import annotations

import logging

from ..config import get_settings
from ..pipeline.embedding import EmbeddingService
from ..pipeline.types import ChunkHit
from ..pipeline.vector_index import VectorIndexService
from .reranker import RerankerService

logger = logging.getLogger("hybrid-retrieval")


class HybridRetrievalService:
    def __init__(
        self,
        embedding: EmbeddingService,
        vector_index: VectorIndexService,
        reranker: RerankerService,
    ) -> None:
        settings = get_settings()
        self._hybrid_top_k = settings.rag_hybrid_top_k
        self._rrf_c = settings.rag_rrf_c
        self._embedding = embedding
        self._vector_index = vector_index
        self._reranker = reranker

    async def retrieve(self, query: str, top_k: int = 5) -> list[ChunkHit]:
        query_vector = await self._embed_query(query)
        fused = await self._vector_index.search_hybrid(
            query=query,
            query_vector=query_vector,
            hybrid_top_k=self._hybrid_top_k,
            rrf_c=self._rrf_c,
        )

        if not fused:
            logger.info("混合检索无结果：queryLength=%s", len(query))
            return []

        reranked = await self._reranker.rerank(query, fused, top_k)
        if reranked:
            return reranked[:top_k]
        return fused[:top_k]

    async def _embed_query(self, query: str) -> list[float] | None:
        try:
            return await self._embedding.embed(query)
        except Exception as err:
            logger.warning("查询向量化失败，仅走关键词：%s", err)
            return None
