"""RabbitMQ 连接服务（aio-pika + connect_robust）。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Awaitable, Callable

import aio_pika
from aio_pika import DeliveryMode, ExchangeType

from ..config import get_settings
from . import constants as c

logger = logging.getLogger("rabbitmq")

MessageHandler = Callable[[aio_pika.IncomingMessage], Awaitable[None]]


class RabbitMqService:
    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = settings.rabbitmq_enabled
        self._url = settings.rabbitmq_url
        self._connect_timeout_ms = settings.rabbitmq_connect_timeout_ms
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: aio_pika.Channel | None = None
        self._handlers: dict[str, MessageHandler] = {}
        self._consumer_tags: list[str] = []

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def channel(self) -> aio_pika.Channel | None:
        return self._channel

    def register_handler(self, queue: str, handler: MessageHandler) -> None:
        self._handlers[queue] = handler

    async def start(self) -> None:
        if not self._enabled:
            logger.warning("RabbitMQ 已禁用（RABBITMQ_ENABLED=false）")
            return

        safe_url = self._redact_amqp_url(self._url)
        logger.info("正在连接 RabbitMQ：%s（超时 %sms）", safe_url, self._connect_timeout_ms)

        try:
            self._connection = await asyncio.wait_for(
                aio_pika.connect_robust(self._url),
                timeout=self._connect_timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"RabbitMQ 连接超时（{self._connect_timeout_ms}ms）：{safe_url}。"
                "请检查服务是否启动、5672 是否被其他容器占用、账号密码是否正确"
            )

        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=10)

        await self._assert_topology(self._channel)
        await self._bind_consumers(self._channel)
        logger.info("RabbitMQ channel 就绪（RAG + Search + KG）")

    async def stop(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def publish(self, exchange: str, routing_key: str, payload: dict) -> bool:
        if not self._enabled or self._channel is None:
            logger.warning(
                "跳过发消息（MQ 不可用）：exchange=%s, rk=%s", exchange, routing_key
            )
            return False
        try:
            exchange_obj = await self._channel.get_exchange(exchange)
            if exchange_obj is None:
                exchange_obj = await self._channel.declare_exchange(
                    exchange, ExchangeType.TOPIC, durable=True
                )
            await exchange_obj.publish(
                aio_pika.Message(
                    body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    content_type="application/json",
                    delivery_mode=DeliveryMode.PERSISTENT,
                ),
                routing_key=routing_key,
            )
            return True
        except Exception as err:
            logger.warning(
                "发消息失败：exchange=%s, rk=%s, error=%s", exchange, routing_key, err
            )
            return False

    async def _assert_topology(self, ch: aio_pika.Channel) -> None:
        rag_ex = await ch.declare_exchange(c.RAG_REINDEX_EXCHANGE, ExchangeType.TOPIC, durable=True)
        rag_q = await ch.declare_queue(c.RAG_REINDEX_QUEUE, durable=True)
        await rag_q.bind(rag_ex, c.RAG_RK_BY_IDS)
        await rag_q.bind(rag_ex, c.RAG_RK_DELETE)

        search_ex = await ch.declare_exchange(c.SEARCH_INDEX_EXCHANGE, ExchangeType.TOPIC, durable=True)
        search_q = await ch.declare_queue(c.SEARCH_INDEX_QUEUE, durable=True)
        await search_q.bind(search_ex, c.SEARCH_RK_INDEX)
        await search_q.bind(search_ex, c.SEARCH_RK_DELETE)

        kg_ex = await ch.declare_exchange(c.KG_GRAPH_EXCHANGE, ExchangeType.TOPIC, durable=True)
        kg_q = await ch.declare_queue(c.KG_GRAPH_QUEUE, durable=True)
        await kg_q.bind(kg_ex, c.KG_RK_BUILD_BY_IDS)
        await kg_q.bind(kg_ex, c.KG_RK_DELETE)

    async def _bind_consumers(self, ch: aio_pika.Channel) -> None:
        for queue_name, handler in self._handlers.items():
            queue = await ch.get_queue(queue_name)
            if queue is None:
                queue = await ch.declare_queue(queue_name, durable=True)

            async def _on_message(message: aio_pika.IncomingMessage, _handler=handler, _queue=queue_name):
                try:
                    await _handler(message)
                    await message.ack()
                except Exception as err:
                    logger.error("消费失败 queue=%s: %s", _queue, err)
                    await message.nack(requeue=False)

            tag = await queue.consume(_on_message, no_ack=False)
            self._consumer_tags.append(tag)
            logger.info("已注册消费者：%s", queue_name)

    @staticmethod
    def _redact_amqp_url(url: str) -> str:
        return re.sub(r"//([^:/@]+):([^@]+)@", r"//\1:***@", url)
