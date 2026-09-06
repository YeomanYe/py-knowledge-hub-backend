"""RAG 对话服务（混合检索 → LLM 作答 → 溯源）。

未登录用户也能提问（不落库）；登录用户写入本人会话。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException
from openai import AsyncOpenAI

from ..config import get_settings
from ..pipeline.types import ChunkHit
from .chat_session import ChatSessionService
from .chat_types import ChatSource
from .hybrid_retrieval import HybridRetrievalService

logger = logging.getLogger("ai-chat")

EXCERPT_LEN = 200
CITATION_RE = re.compile(r"\[(\d+)\]")

SYSTEM_PROMPT = (
    "你是企业知识库助手。只根据「检索到的资料」回答用户问题。"
    "若资料不足以回答，明确说不知道，不要编造。"
    "凡是依据某条资料作出的陈述，必须在句末标注对应编号，如 [1]、[2]。"
    "编号必须与资料列表一致，不要标注未使用的编号，不要编造文档标题或链接。"
    "回答简洁，必要时列出条目。"
)


class AiChatService:
    def __init__(
        self,
        retrieval: HybridRetrievalService,
        sessions: ChatSessionService,
    ) -> None:
        settings = get_settings()
        self._retrieval = retrieval
        self._sessions = sessions
        self._llm: AsyncOpenAI | None = None

        api_key = (
            settings.openai_api_key
            or settings.llm_api_key
            or settings.dashscope_api_key
            or ""
        )
        if not api_key:
            return

        base_url = (
            settings.openai_base_url
            or settings.llm_base_url
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        model = settings.model_name or settings.llm_model or "qwen-plus"
        self._model = model
        self._timeout_ms = settings.ai_chat_timeout_ms
        self._llm = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=self._timeout_ms / 1000)

    async def chat(
        self,
        question: str,
        top_k: int = 5,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        trimmed = question.strip()
        if not trimmed:
            return {
                "sessionId": session_id,
                "answer": "请输入问题。",
                "sources": [],
            }

        hits = await self._retrieval.retrieve(trimmed, top_k)

        if not hits:
            empty = {"answer": "知识库里没有相关内容。", "sources": []}
            session = (
                await self._sessions.append_turn(
                    user_id, session_id, trimmed, empty["answer"], empty["sources"]
                )
                if user_id
                else None
            )
            return {
                "sessionId": session.id if session else (session_id or None),
                **empty,
            }

        if self._llm is None:
            raise HTTPException(
                503,
                "未配置 OPENAI_API_KEY / LLM_API_KEY / DASHSCOPE_API_KEY，无法生成回答",
            )

        context = self._build_context(hits)
        response = await self._llm.chat.completions.create(
            model=self._model,
            temperature=0.2,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"检索到的资料：\n{context}\n\n用户问题：{trimmed}",
                },
            ],
        )

        content = response.choices[0].message.content
        answer = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

        sources = self._to_cited_sources(answer, hits)
        logger.info(
            "RAG 对话完成：hits=%s, cited=%s, answerLength=%s",
            len(hits), len(sources), len(answer),
        )

        session = (
            await self._sessions.append_turn(user_id, session_id, trimmed, answer, sources)
            if user_id
            else None
        )
        return {
            "sessionId": session.id if session else (session_id or None),
            "answer": answer,
            "sources": [s.__dict__ for s in sources],
        }

    def _to_cited_sources(self, answer: str, hits: list[ChunkHit]) -> list[ChatSource]:
        cited: set[int] = set()
        for match in CITATION_RE.finditer(answer):
            n = int(match.group(1))
            if 1 <= n <= len(hits):
                cited.add(n)
        indexes = sorted(cited) if cited else list(range(1, len(hits) + 1))
        return [self._to_source(index, hits[index - 1]) for index in indexes]

    @staticmethod
    def _to_source(index: int, hit: ChunkHit) -> ChatSource:
        return ChatSource(
            index=index,
            documentId=hit.documentId,
            documentTitle=hit.documentTitle,
            heading=hit.heading,
            excerpt=AiChatService._excerpt(hit.content),
            score=hit.score,
        )

    @staticmethod
    def _excerpt(content: str) -> str:
        text = " ".join(content.split()).strip()
        if len(text) <= EXCERPT_LEN:
            return text
        return f"{text[:EXCERPT_LEN]}..."

    @staticmethod
    def _build_context(hits: list[ChunkHit]) -> str:
        parts = []
        for i, src in enumerate(hits):
            heading = f" / {src.heading}" if src.heading else ""
            snippet = f"{src.content[:800]}..." if len(src.content) > 800 else src.content
            parts.append(f"[{i + 1}] {src.documentTitle}{heading}\n{snippet}")
        return "\n\n".join(parts)
