"""文本 reranker（DashScope text-rerank）。

对 RRF 粗排后的候选块打相关性分。未配置或调用失败时返回 None，由上层降级为 RRF 顺序。
"""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings
from ..pipeline.types import ChunkHit

logger = logging.getLogger("reranker")


class RerankerService:
    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = settings.rag_rerank_enabled
        self._api_key = (
            settings.rerank_api_key
            or settings.dashscope_api_key
            or settings.openai_api_key
            or ""
        )
        self._model = settings.rag_rerank_model
        host = (settings.rerank_base_url or "https://dashscope.aliyuncs.com").rstrip("/")
        self._endpoint = f"{host}/api/v1/services/rerank/text-rerank/text-rerank"

    def is_enabled(self) -> bool:
        return self._enabled and bool(self._api_key)

    async def rerank(
        self, query: str, candidates: list[ChunkHit], top_n: int
    ) -> list[ChunkHit] | None:
        if not candidates:
            return []
        if not self.is_enabled():
            logger.warning("Reranker 未启用或未配置 Key，跳过重排")
            return None

        documents = [self._to_document(hit) for hit in candidates]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "input": {"query": query, "documents": documents},
                        "parameters": {
                            "return_documents": False,
                            "top_n": min(top_n, len(candidates)),
                        },
                    },
                )
                body = response.json()

            if response.status_code >= 400:
                logger.warning(
                    "Rerank 调用失败：status=%s, code=%s, message=%s",
                    response.status_code,
                    (body or {}).get("code", ""),
                    (body or {}).get("message", ""),
                )
                return None

            results = ((body or {}).get("output") or {}).get("results") or []
            if not results:
                logger.warning("Rerank 返回空结果，降级为 RRF")
                return None

            reranked: list[ChunkHit] = []
            for item in results:
                index = item.get("index", -1)
                if not (0 <= index < len(candidates)):
                    continue
                hit = candidates[index]
                hit.score = float(item.get("relevance_score", 0))
                reranked.append(hit)

            logger.info(
                "Rerank 完成：model=%s, in=%s, out=%s",
                self._model, len(candidates), len(reranked),
            )
            return reranked
        except Exception as err:
            logger.warning("Rerank 异常，降级为 RRF：%s", err)
            return None

    @staticmethod
    def _to_document(hit: ChunkHit) -> str:
        heading = f"{hit.heading}\n" if hit.heading else ""
        text = f"{hit.documentTitle}\n{heading}{hit.content}".strip()
        return f"{text[:2000]}..." if len(text) > 2000 else text
