"""流式 Agentic RAG（SSE，UI Message Stream 协议）。

前端 useChat 走 POST /ai/chat/stream；事件逐行输出：
start / data-session / data-status / data-intent / data-eval / data-sources /
source-document / data-graph / text-start / text-delta / text-end / error / finish
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

from ..config import get_settings
from ..document_access import access_from_user
from .agentic_retrieve import retrieve_until_relevant
from .chat_long_memory import ChatLongMemoryService
from .chat_memory_util import db_rows_to_messages
from .chat_short_memory import ChatShortMemoryService
from .chat_session import ChatSessionService
from .hybrid_retrieval import HybridRetrievalService
from .query_rewrite import ChatQueryRewriteService
from .web_search import WebSearchService

logger = logging.getLogger("ai-stream")

EXCERPT_LEN = 200
CITATION_RE = re.compile(r"\[(\d+)\]")

SYSTEM_BASE = (
    "你是企业知识库助手，必须按 Agentic RAG 循环作答，不要跳步。"
    "结合对话历史和记忆里的用户背景，但制度/流程以本轮检索资料为准。"
)


class AiStreamService:
    def __init__(
        self,
        retrieval: HybridRetrievalService,
        sessions: ChatSessionService,
        web_search: WebSearchService,
        short_memory: ChatShortMemoryService,
        long_memory: ChatLongMemoryService,
        query_rewrite: ChatQueryRewriteService,
        graph,
    ) -> None:
        settings = get_settings()
        self._retrieval = retrieval
        self._sessions = sessions
        self._web_search = web_search
        self._short_memory = short_memory
        self._long_memory = long_memory
        self._query_rewrite = query_rewrite
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
        self._enable_thinking = settings.llm_enable_thinking
        self._llm = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=self._timeout_ms / 1000)

    def stream_chat(self, dto, user: dict) -> StreamingResponse:
        return StreamingResponse(
            self._run(dto, user),
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------ 主流程
    async def _run(self, dto, user: dict) -> AsyncIterator[str]:
        question = _last_user_text(dto.messages)
        top_k = dto.topK or 5
        persist_session_id = dto.sessionId
        persist_sources: list[dict] = []
        persist_history: list[dict] = []

        yield "start\n"

        if not question:
            yield _event("text-start", {"id": "empty"})
            yield _event("text-delta", {"id": "empty", "delta": "请输入问题。"})
            yield _event("text-end", {"id": "empty"})
            yield "finish\n"
            return

        try:
            if dto.sessionId:
                session = await self._sessions.touch_title(
                    user["userId"], dto.sessionId, question
                )
            else:
                session = await self._sessions.create(
                    user["userId"], _title_from_question(question)
                )
            persist_session_id = session.id
            yield _event("data-session", {"data": {"sessionId": session.id}})

            history = await self._load_working_history(user["userId"], session.id)
            persist_history = history

            yield _event(
                "data-status", {"data": {"stage": "intent", "text": "正在识别意图…"}}
            )
            plan = await self._query_rewrite.classify(question, history)
            yield _event(
                "data-intent",
                {
                    "data": {
                        "intent": plan["intent"],
                        "label": plan["label"],
                        "query": plan["query"],
                        "graphQueries": plan["graphQueries"],
                        "allowRetrieve": plan["allowRetrieve"],
                        "allowGraph": plan["allowGraph"],
                        "allowWeb": plan["allowWeb"],
                    }
                },
            )

            if self._llm is None:
                yield _event("error", {"errorText": "未配置 LLM Key，无法生成回答"})
                yield "finish\n"
                return

            mem_hits_p = self._long_memory.search(
                user["userId"], session.id, question
            )

            # —— 确定性 Agent 编排：知识库与图谱并行检索 ——
            access = access_from_user(user)
            yield _event("data-status", {"data": {"stage": "retrieve", "text": "正在检索知识库…"}})

            self._pending_events: list[str] = []

            async def _eval_cb(data: dict):
                self._pending_events.append(_event("data-eval", {"data": data}))

            async def _rewrite_cb(query: str):
                self._pending_events.append(
                    _event(
                        "data-status",
                        {"data": {"stage": "rewrite", "text": f"正在改写检索词…"}},
                    )
                )

            kb_task = (
                retrieve_until_relevant(
                    question=question,
                    query=plan["query"],
                    top_k=top_k,
                    user=user,
                    retrieval=self._retrieval,
                    rewrite=self._query_rewrite,
                    on_eval=_eval_cb,
                    on_rewrite=_rewrite_cb,
                )
                if plan["allowRetrieve"]
                else None
            )
            graph_task = (
                self._graph.retrieve_for_chat(plan["graphQueries"], 8, access)
                if plan["allowGraph"] and plan["graphQueries"]
                else None
            )

            from asyncio import gather

            results = await gather(
                kb_task if kb_task is not None else _none(),
                graph_task if graph_task is not None else _none(),
            )
            retrieved, graph_hit = results

            hits = retrieved["hits"] if retrieved else []
            kb_insufficient = bool(retrieved and not retrieved["eval"]["ok"])
            search_query = (
                retrieved["usedQuery"] if retrieved and retrieved["usedQuery"] else plan["query"] or question
            )
            has_graph = bool(
                (graph_hit or {}).get("entities") or (graph_hit or {}).get("relations")
            )
            if graph_hit and has_graph:
                yield _event("data-graph", {"data": graph_hit})

            for ev in self._pending_events:
                yield ev
            self._pending_events = []

            if hits:
                async for ev in self._emit_sources(self._build_sources(hits, persist_sources)):
                    yield ev

            # —— 联网 ——
            web = None
            if plan["allowWeb"] and (not plan["allowRetrieve"] or kb_insufficient):
                yield _event(
                    "data-status",
                    {"data": {"stage": "web", "text": "正在联网搜索…"}},
                )
                web = await self._web_search.search(search_query)

            mem_hits = await mem_hits_p
            memory_msg = self._long_memory.build_system_message(mem_hits)
            graph_msg = _graph_system_text(graph_hit) if graph_hit else ""
            if plan["intent"] == "kb" and kb_insufficient and not has_graph and not web:
                final_answer = "知识库里没有相关内容。"
                yield _event("text-start", {"id": "empty"})
                yield _event("text-delta", {"id": "empty", "delta": final_answer})
                yield _event("text-end", {"id": "empty"})
                yield "finish\n"
                await self._persist_turn(
                    user, persist_session_id, persist_history, question, final_answer, []
                )
                return

            messages: list[dict] = [
                {"role": "system", "content": self._system_for_plan(plan)}
            ]
            if memory_msg:
                messages.append({"role": "system", "content": memory_msg})
            if graph_msg:
                messages.append({"role": "system", "content": graph_msg})
            messages.extend(history)
            user_turn_parts: list[str] = []
            if hits:
                user_turn_parts.append(f"知识库资料：\n{self._build_context(hits)}")
            if web is not None:
                user_turn_parts.append(f"联网结果：\n{self._build_web_context(web)}")
            user_turn_parts.append(f"用户问题：{question}")
            messages.append({"role": "user", "content": "\n\n".join(user_turn_parts)})

            # —— LLM 流式生成 ——
            extra: dict[str, Any] = {}
            if self._enable_thinking:
                extra["enable_thinking"] = True
            stream = await self._llm.chat.completions.create(
                model=self._model,
                temperature=0.2,
                max_tokens=2048,
                messages=messages,
                stream=True,
                extra_body=extra if extra else None,
            )

            text_id = "empty"
            yield _event("text-start", {"id": text_id})
            answer_parts: list[str] = []
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if not piece:
                    continue
                answer_parts.append(piece)
                yield _event("text-delta", {"id": text_id, "delta": piece})
            yield _event("text-end", {"id": text_id})

            answer = "".join(answer_parts).strip()
            final_answer = answer or "未能生成回答。"
            used = {int(m.group(1)) for m in CITATION_RE.finditer(final_answer)}
            sources = (
                [s for s in persist_sources if s.get("index") in used]
                if used
                else []
            )
            await self._persist_turn(
                user,
                persist_session_id,
                persist_history,
                question,
                final_answer,
                sources,
            )
            yield "finish\n"
        except Exception as err:
            logger.warning("流式对话失败：%s", err)
            try:
                yield _event("error", {"errorText": str(err)})
            finally:
                yield "finish\n"

    # ------------------------------------------------------------ 内部
    async def _persist_turn(
        self,
        user: dict,
        session_id: str,
        history: list[dict],
        question: str,
        answer: str,
        sources: list[dict],
    ) -> None:
        try:
            await self._sessions.append_turn(
                user["userId"], session_id, question, answer, sources
            )
            await self._short_memory.append_turn(
                user["userId"], session_id, history, question, answer
            )
        except Exception as err:
            logger.warning("流式对话落库失败：%s", err)
        await self._long_memory.remember_turn(
            user["userId"], session_id, question, answer
        )

    async def _load_working_history(self, user_id: str, session_id: str) -> list[dict]:
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

    def _build_sources(self, hits, persist_sources: list[dict]) -> list[dict]:
        offset = len(persist_sources)
        sources = []
        for i, hit in enumerate(hits):
            sources.append(
                {
                    "index": offset + i + 1,
                    "documentId": hit.documentId,
                    "documentTitle": hit.documentTitle,
                    "heading": hit.heading,
                    "excerpt": _excerpt(hit.content),
                    "score": hit.score,
                }
            )
        persist_sources.extend(sources)
        return sources

    async def _emit_sources(self, sources: list[dict]) -> AsyncIterator[str]:
        yield _event("data-sources", {"data": sources})
        for src in sources:
            yield _event(
                "source-document",
                {
                    "sourceId": src["documentId"],
                    "mediaType": "text/markdown",
                    "title": f"[{src['index']}] {src['documentTitle']}",
                },
            )

    @staticmethod
    def _build_context(hits) -> str:
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

    def _system_for_plan(self, plan: dict) -> str:
        prompt = SYSTEM_BASE + f"本轮意图：{plan['label']}。"
        if plan["allowRetrieve"] and plan["allowGraph"]:
            graph_hint = (
                " / ".join(plan["graphQueries"])
                if plan["graphQueries"]
                else "从问题抽出的短实体名"
            )
            prompt += (
                f"知识库建议检索词「{plan['query']}」，图谱建议检索词「{graph_hint}」。"
                "第一步必须同时并行检索知识库与图谱：不要先查库再决定是否查图，不要只查其中一个。"
                "图谱只看实体关系、不能当制度原文。"
                "循环：① 并行检索；② 若知识库 eval.ok 则用文档 context 作答并标 [n]；"
                "③ 若 insufficient，先改写检索词，再用新词再查（知识库最多两次）；图谱词变了才再查图；"
                "④ 两次后仍不足再考虑联网。eval.ok 后不要再检索、不要改写。不要第三次检索知识库。"
                "图谱为空不要编造关系。不要用记忆替代文档。"
            )
        elif plan["allowRetrieve"]:
            prompt += (
                f"建议检索词「{plan['query']}」，可改写成更准的 query。"
                "循环：① 检索知识库；② 若 eval.ok 则用 context 作答并标 [n]；"
                "③ 若 insufficient，先改写，再用新词再查（知识库最多两次）；"
                "④ 两次后仍不足再考虑联网。eval.ok 后不要再检索、不要改写。不要第三次检索知识库。"
                "不要用记忆替代文档。"
            )
        else:
            prompt += "不要检索知识库。"
        if not plan["allowGraph"]:
            prompt += "不要检索图谱。"
        if plan["intent"] == "chitchat":
            prompt += "这是闲聊，直接回应，不要调用任何工具。"
        if plan["intent"] == "profile":
            prompt += "这是个人偏好或身份问题，根据记忆回答，不要检索知识库。"
        if plan["intent"] == "kb":
            prompt += (
                "两次检索后仍不足：明确说知识库没有相关内容，禁止编造制度，不要联网。"
            )
        if plan["intent"] == "web":
            prompt += "用联网结果回答，用标题+链接说明，不要编造。"
        if plan["intent"] == "kb_then_web":
            prompt += (
                "先走完知识库循环；两次后仍不足再联网。联网结果用标题+链接说明，不要把网页写成内部制度。"
            )
        if not plan["allowWeb"]:
            prompt += "不要联网搜索。"
        prompt += "资料不够就明确说不知道。回答简洁，必要时列出条目。"
        return prompt


async def _none():
    return None


def _last_user_text(messages) -> str:
    if not messages:
        return ""
    for msg in reversed(messages):
        role = getattr(msg, "role", None)
        if role is None and isinstance(msg, dict):
            role = msg.get("role")
        if role != "user":
            continue
        parts = getattr(msg, "parts", None)
        if parts is None and isinstance(msg, dict):
            parts = msg.get("parts")
        text = "".join(
            str(p.get("text") or "")
            for p in (parts or [])
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        )
        return text.strip()
    return ""


def _title_from_question(question: str) -> str:
    text = " ".join(question.split()).strip()
    return f"{text[:30]}…" if len(text) > 30 else text


def _excerpt(content: str) -> str:
    text = " ".join(content.split()).strip()
    if len(text) <= EXCERPT_LEN:
        return text
    return f"{text[:EXCERPT_LEN]}..."


def _graph_system_text(hit: dict) -> str:
    from ..pipeline.graph_build import format_graph_system_text

    return format_graph_system_text(hit)


def _event(type_: str, data: dict | None = None) -> str:
    if data is None:
        return f"{type_}\n"
    return f"{type_}:{json.dumps(data, ensure_ascii=False)}\n"
