"""非流式 Agentic RAG 对话服务。

意图路由 → 知识库与图谱并行检索（不足则改写再查）→ 按需联网 → 作答。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException
from openai import AsyncOpenAI

from ..config import get_settings
from ..document_access import access_from_user
from ..pipeline.types import ChunkHit
from .agentic_retrieve import retrieve_until_relevant
from .chat_long_memory import ChatLongMemoryService
from .chat_memory_util import db_rows_to_messages
from .chat_short_memory import ChatShortMemoryService
from .chat_session import ChatSessionService
from .chat_types import ChatSource
from .hybrid_retrieval import HybridRetrievalService
from .query_rewrite import ChatQueryRewriteService
from .web_search import WebSearchService

logger = logging.getLogger("ai-chat")

EXCERPT_LEN = 200
CITATION_RE = re.compile(r"\[(\d+)\]")


class AiChatService:
    def __init__(
        self,
        retrieval: HybridRetrievalService,
        sessions: ChatSessionService,
        short_memory: ChatShortMemoryService,
        long_memory: ChatLongMemoryService,
        query_rewrite: ChatQueryRewriteService,
        web_search: WebSearchService,
        graph,
    ) -> None:
        settings = get_settings()
        self._retrieval = retrieval
        self._sessions = sessions
        self._short_memory = short_memory
        self._long_memory = long_memory
        self._query_rewrite = query_rewrite
        self._web_search = web_search
        self._graph = graph
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
        user: dict | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        trimmed = question.strip()
        if not trimmed:
            return {
                "sessionId": session_id,
                "answer": "请输入问题。",
                "sources": [],
            }

        history = (
            await self._load_working_history(user["userId"], session_id)
            if user
            else []
        )
        plan = await self._query_rewrite.classify(trimmed, history)
        mem_hits_p = (
            self._long_memory.search(user["userId"], session_id, plan["query"])
            if user
            else _empty_mem_hits()
        )
        access = access_from_user(user) if user else None
        kb_p = (
            retrieve_until_relevant(
                question=trimmed,
                query=plan["query"],
                top_k=top_k,
                user=user,
                retrieval=self._retrieval,
                rewrite=self._query_rewrite,
            )
            if plan["allowRetrieve"]
            else _resolved_none()
        )
        graph_p = (
            self._graph.retrieve_for_chat(plan["graphQueries"], 8, access)
            if plan["allowGraph"] and user and access and plan["graphQueries"]
            else _resolved_none()
        )
        retrieved, graph_hit = await _gather(kb_p, graph_p)

        hits: list[ChunkHit] = []
        kb_insufficient = False
        search_query = plan["query"] or trimmed
        if retrieved is not None:
            hits = retrieved["hits"]
            kb_insufficient = not retrieved["eval"]["ok"]
            search_query = retrieved["usedQuery"] or search_query
        has_graph = bool(
            (graph_hit or {}).get("entities") or (graph_hit or {}).get("relations")
        )
        web = None
        if plan["allowWeb"] and (not plan["allowRetrieve"] or kb_insufficient):
            web = await self._web_search.search(search_query)
        mem_hits = await mem_hits_p

        # 纯知识库问题且检索不足、图谱也无命中：明确说没有
        if plan["intent"] == "kb" and kb_insufficient and not has_graph:
            empty = {"answer": "知识库里没有相关内容。", "sources": []}
            session = (
                await self._sessions.append_turn(
                    user["userId"], session_id, trimmed, empty["answer"], empty["sources"]
                )
                if user
                else None
            )
            if user and session:
                await self._short_memory.append_turn(
                    user["userId"], session.id, history, trimmed, empty["answer"]
                )
                await self._long_memory.remember_turn(
                    user["userId"], session.id, trimmed, empty["answer"]
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

        memory_msg = self._long_memory.build_system_message(mem_hits)
        graph_msg = _format_graph_system_text(graph_hit) if graph_hit else ""
        parts: list[str] = []
        if hits:
            parts.append(f"知识库资料：\n{self._build_context(hits)}")
        if web is not None:
            parts.append(f"联网结果：\n{self._build_web_context(web)}")
        parts.append(f"用户问题：{trimmed}")
        user_turn = "\n\n".join(parts)

        messages: list[dict] = [{"role": "system", "content": self._build_system_prompt(plan, bool(hits), web)}]
        if memory_msg:
            messages.append({"role": "system", "content": memory_msg})
        if graph_msg:
            messages.append({"role": "system", "content": graph_msg})
        messages.extend(history)
        messages.append({"role": "user", "content": user_turn})

        response = await self._llm.chat.completions.create(
            model=self._model,
            temperature=0.2,
            max_tokens=2048,
            messages=messages,
        )

        content = response.choices[0].message.content
        answer = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

        sources = self._to_cited_sources(answer, hits)
        logger.info(
            "RAG 对话完成：hits=%s, cited=%s, answerLength=%s",
            len(hits), len(sources), len(answer),
        )

        session = (
            await self._sessions.append_turn(
                user["userId"], session_id, trimmed, answer, sources
            )
            if user
            else None
        )
        if user and session:
            await self._short_memory.append_turn(
                user["userId"], session.id, history, trimmed, answer
            )
            await self._long_memory.remember_turn(
                user["userId"], session.id, trimmed, answer
            )

        return {
            "sessionId": session.id if session else (session_id or None),
            "answer": answer,
            "sources": [s.__dict__ for s in sources],
        }

    # ------------------------------------------------------------ 内部
    async def _load_working_history(self, user_id: str, session_id: str | None) -> list[dict]:
        if not session_id:
            return []
        cached = await self._short_memory.try_load(user_id, session_id)
        if cached:
            return cached
        rows = await self._sessions.list_recent_messages(
            user_id, session_id, self._short_memory.window_size
        )
        history = db_rows_to_messages(rows)
        if history:
            await self._short_memory.save(user_id, session_id, history)
            logger.info(
                "短期记忆从库回填：sessionId=%s, n=%s", session_id, len(history)
            )
        return history

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

    @staticmethod
    def _build_web_context(web: dict) -> str:
        if web.get("error"):
            return web["error"]
        if not web.get("items"):
            return "无结果。"
        return "\n\n".join(
            f"{i + 1}. {hit['title']}\n{hit['url']}\n{hit['snippet']}"
            for i, hit in enumerate(web["items"])
        )

    @staticmethod
    def _build_system_prompt(plan: dict, has_kb: bool, web: dict | None = None) -> str:
        prompt = (
            "你是企业知识库助手。结合对话历史和记忆里的用户背景回答。"
            "制度/流程以本轮知识库资料为准，不要用记忆替代文档。"
        )
        if has_kb:
            prompt += (
                "凡是依据某条资料作出的陈述，必须在句末标注对应编号，如 [1]、[2]。"
                "编号必须与资料列表一致，不要标注未使用的编号，不要编造文档标题或链接。"
            )
        elif plan["intent"] in ("kb", "kb_then_web"):
            prompt += "知识库没有切题资料，不要编造内部制度。"
        if web and web.get("items"):
            prompt += "联网结果只作公开信息补充，用标题+链接说明，不要写成公司内部规定。"
        if plan["intent"] in ("chitchat", "profile"):
            prompt += "可回应寒暄或个人偏好，不要编造制度。"
        prompt += "若资料不足以回答，明确说不知道。回答简洁，必要时列出条目。"
        return prompt


def _format_graph_system_text(hit: dict) -> str:
    from ..pipeline.graph_build import format_graph_system_text

    return format_graph_system_text(hit)


async def _gather(kb_p, graph_p):
    from asyncio import gather

    return await gather(kb_p, graph_p)


async def _resolved_none():
    return None


async def _empty_mem_hits():
    return {"user": [], "session": []}
