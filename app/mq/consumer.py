"""文档发布后管线的 MQ 消费者。"""
from __future__ import annotations

import json
import logging

import aio_pika

from ..pipeline.orchestrator import PipelineOrchestrator
from . import constants as c
from .rabbitmq import RabbitMqService

logger = logging.getLogger("pipeline-consumer")


class DocumentPipelineConsumer:
    def __init__(self, rabbit: RabbitMqService, orchestrator: PipelineOrchestrator) -> None:
        self._rabbit = rabbit
        self._orchestrator = orchestrator
        # 构造函数注册，保证早于 rabbit.start() 的 bind_consumers
        rabbit.register_handler(c.RAG_REINDEX_QUEUE, self.handle_rag)
        rabbit.register_handler(c.SEARCH_INDEX_QUEUE, self.handle_search)
        rabbit.register_handler(c.KG_GRAPH_QUEUE, self.handle_kg)

    async def handle_rag(self, message: aio_pika.IncomingMessage) -> None:
        body = self._parse(message)
        logger.info(
            "[RAG] type=%s, taskId=%s, documentIds=%s",
            body.get("type"), body.get("taskId"), body.get("documentIds") or [],
        )
        await self._orchestrator.handle_rag_reindex(
            body.get("type"), body.get("documentIds") or []
        )

    async def handle_search(self, message: aio_pika.IncomingMessage) -> None:
        body = self._parse(message)
        logger.info(
            "[Search] type=%s, taskId=%s, documentId=%s",
            body.get("type"), body.get("taskId"), body.get("documentId"),
        )
        await self._orchestrator.handle_search_index(
            body.get("type"), body.get("documentId")
        )

    async def handle_kg(self, message: aio_pika.IncomingMessage) -> None:
        body = self._parse(message)
        logger.info(
            "[KG] type=%s, taskId=%s, documentIds=%s",
            body.get("type"), body.get("taskId"), body.get("documentIds") or [],
        )
        await self._orchestrator.handle_kg_build(
            body.get("type"), body.get("documentIds") or []
        )

    @staticmethod
    def _parse(message: aio_pika.IncomingMessage) -> dict:
        return json.loads(message.body.decode("utf-8"))
