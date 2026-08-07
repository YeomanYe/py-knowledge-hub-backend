"""文档发布后知识管线「生产者」。

触发：RAG 向量化 + Search 全文索引 + KG 建图。
约定：投递失败只打日志，不回滚文档已发布状态。
"""
from __future__ import annotations

import logging
import uuid

from . import constants as c
from .messages import KgBuildMessage, ReindexMessage, SearchIndexMessage
from .rabbitmq import RabbitMqService

logger = logging.getLogger("pipeline-publisher")


class DocumentPipelinePublisher:
    def __init__(self, rabbit: RabbitMqService) -> None:
        self._rabbit = rabbit

    async def after_publish(self, document_id: str) -> None:
        import asyncio

        await asyncio.gather(
            self.trigger_rag_reindex(document_id),
            self.trigger_search_index(document_id),
            self.trigger_kg_build(document_id),
        )

    async def after_unpublish(self, document_id: str) -> None:
        import asyncio

        await asyncio.gather(
            self.trigger_rag_delete(document_id),
            self.trigger_search_delete(document_id),
            self.trigger_kg_delete(document_id),
        )

    async def trigger_rag_reindex(self, document_id: str) -> None:
        message = ReindexMessage(
            taskId=str(uuid.uuid4()), type="BY_DOC_IDS", documentIds=[document_id]
        )
        ok = await self._rabbit.publish(
            c.RAG_REINDEX_EXCHANGE, c.RAG_RK_BY_IDS, _to_dict(message)
        )
        logger.info(
            "RAG 重建索引%s：documentId=%s, taskId=%s",
            "已投递" if ok else "投递失败",
            document_id,
            message.taskId,
        )

    async def trigger_rag_delete(self, document_id: str) -> None:
        message = ReindexMessage(
            taskId=str(uuid.uuid4()), type="DELETE_BY_DOC_IDS", documentIds=[document_id]
        )
        await self._rabbit.publish(
            c.RAG_REINDEX_EXCHANGE, c.RAG_RK_DELETE, _to_dict(message)
        )

    async def trigger_search_index(self, document_id: str) -> None:
        message = SearchIndexMessage(
            taskId=str(uuid.uuid4()), type="INDEX", documentId=document_id
        )
        ok = await self._rabbit.publish(
            c.SEARCH_INDEX_EXCHANGE, c.SEARCH_RK_INDEX, _to_dict(message)
        )
        logger.info(
            "ES 搜索索引%s：documentId=%s, taskId=%s",
            "已投递" if ok else "投递失败",
            document_id,
            message.taskId,
        )

    async def trigger_search_delete(self, document_id: str) -> None:
        message = SearchIndexMessage(
            taskId=str(uuid.uuid4()), type="DELETE", documentId=document_id
        )
        await self._rabbit.publish(
            c.SEARCH_INDEX_EXCHANGE, c.SEARCH_RK_DELETE, _to_dict(message)
        )

    async def trigger_kg_build(self, document_id: str) -> None:
        message = KgBuildMessage(
            taskId=str(uuid.uuid4()), type="BUILD_BY_DOC_IDS", documentIds=[document_id]
        )
        ok = await self._rabbit.publish(
            c.KG_GRAPH_EXCHANGE, c.KG_RK_BUILD_BY_IDS, _to_dict(message)
        )
        logger.info(
            "KG 建图%s：documentId=%s, taskId=%s",
            "已投递" if ok else "投递失败",
            document_id,
            message.taskId,
        )

    async def trigger_kg_delete(self, document_id: str) -> None:
        message = KgBuildMessage(
            taskId=str(uuid.uuid4()), type="DELETE_BY_DOC_IDS", documentIds=[document_id]
        )
        await self._rabbit.publish(
            c.KG_GRAPH_EXCHANGE, c.KG_RK_DELETE, _to_dict(message)
        )


def _to_dict(message) -> dict:
    return {
        "taskId": message.taskId,
        "type": message.type,
        "documentId": getattr(message, "documentId", None),
        "documentIds": getattr(message, "documentIds", None),
    }
