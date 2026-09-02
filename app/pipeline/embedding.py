"""文本向量化服务（OpenAI 兼容 API）。

DashScope text-embedding-v3 单次最多 10 条，超过钳制为 10 并告警。
API Key 优先 EMBEDDING_API_KEY → DASHSCOPE_API_KEY → OPENAI_API_KEY。
"""
from __future__ import annotations

import logging

from openai import AsyncOpenAI

from ..config import get_settings

logger = logging.getLogger("embedding")


class EmbeddingService:
    def __init__(self) -> None:
        settings = get_settings()
        self._dimension = settings.embedding_dimension
        configured_batch = settings.embedding_batch_size
        self._batch_size = min(configured_batch if configured_batch > 0 else 10, 10)
        if configured_batch > 10:
            logger.warning(
                "EMBEDDING_BATCH_SIZE=%s 超过 DashScope 上限，已钳制为 10", configured_batch
            )

        api_key = (
            settings.embedding_api_key
            or settings.dashscope_api_key
            or settings.openai_api_key
        )
        if not api_key:
            # 与 LangChain OpenAIEmbeddings 一致：未配 Key 构造不报错，调用时失败
            self._client = None
            self._model = settings.embedding_model
            return

        base_url = settings.embedding_base_url
        model = settings.embedding_model
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _require_client(self) -> AsyncOpenAI:
        if self._client is None:
            raise RuntimeError(
                "未配置 EMBEDDING_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY"
            )
        return self._client

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._require_client()
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            response = await client.embeddings.create(
                model=self._model, input=batch, dimensions=self._dimension
            )
            # 按请求顺序取向量
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
        logger.debug("嵌入完成：count=%s", len(vectors))
        return vectors
