"""对话长期记忆（Mem0 HTTP API）。未配置 MEM0_API_KEY 时全部跳过。

记忆只用于改写问题和补上下文，不作制度事实来源。
"""
from __future__ import annotations

import json
import logging

import aiohttp
from openai import AsyncOpenAI

from ..config import get_settings

logger = logging.getLogger("chat-long-memory")

CLASSIFIER_PROMPT = (
    "你是企业知识库助手的记忆分类器。判断本轮是否有「新事实」要写入 Mem0。\n"
    "\n"
    "## user 层（跨会话）\n"
    "- 用户身份、岗位、所在团队自称\n"
    "- 长期偏好：答短一点、只要本团队制度、技术回答带示例\n"
    "- 持久约束：过敏、语言、称呼\n"
    "\n"
    "## session 层（仅当前会话）\n"
    "- 正在排查的问题、本次要写的文档、已确认的下一步\n"
    "- 用户说「这次」「本轮」的工作上下文\n"
    "\n"
    "## 均不写入\n"
    "- 寒暄、致谢、纯确认\n"
    "- 助手根据知识库/检索资料说出的制度、流程、负责人、系统名（那是文档事实，不是用户记忆）\n"
    "- 联网搜索结果、引用编号 [n]\n"
    "- 无信息增量的复述\n"
    "\n"
    "## 原则\n"
    "1. 知识库内容永远不要写成 user 记忆\n"
    "2. 「这次先看差旅制度第三节」→ session，不要标成 user\n"
    "3. user 与 session 可同时为 true\n"
    "4. 一次性提问且未产生需跨轮记住的约定 → 均为 false\n"
    "\n"
    "只输出 JSON：{\"write_user\": bool, \"write_session\": bool, \"reason\": \"一句中文理由\"}"
)


class ChatLongMemoryService:
    def __init__(self) -> None:
        settings = get_settings()
        self._top_k = settings.mem0_top_k
        self._api_key = (settings.mem0_api_key or "").strip()
        self._host = (settings.mem0_host or "https://api.mem0.ai").rstrip("/")
        self._classifier: AsyncOpenAI | None = None
        self._classifier_model: str = ""

        api_key = (
            settings.openai_api_key
            or settings.llm_api_key
            or settings.dashscope_api_key
            or ""
        )
        if api_key and self._api_key:
            base_url = (
                settings.openai_base_url
                or settings.llm_base_url
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            model = settings.model_name or settings.llm_model or "qwen-plus"
            self._classifier = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=settings.ai_chat_timeout_ms / 1000,
            )
            self._classifier_model = model
        if not self._api_key:
            logger.warning("未配置 MEM0_API_KEY，跳过长期记忆")

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def search(
        self, user_id: str, session_id: str | None, query: str
    ) -> dict:
        empty = {"user": [], "session": []}
        if not self._api_key:
            return empty
        try:
            user_result = await self._mem0_search(
                {"user_id": user_id}, query
            )
            session_result = (
                await self._mem0_search(
                    {"AND": [{"user_id": user_id}, {"run_id": session_id}]},
                    query,
                )
                if session_id
                else {"results": []}
            )
            return {
                "user": [m for m in user_result.get("results", []) if m],
                "session": [m for m in session_result.get("results", []) if m],
            }
        except Exception as err:
            logger.warning("Mem0 检索失败：%s", err)
            return empty

    def build_system_message(self, hits: dict) -> str | None:
        blocks: list[str] = []
        if hits.get("user"):
            blocks.append(
                "【用户长期记忆】\n" + "\n".join(f"- {line}" for line in hits["user"])
            )
        if hits.get("session"):
            blocks.append(
                "【当前会话记忆】\n"
                + "\n".join(f"- {line}" for line in hits["session"])
            )
        if not blocks:
            return None
        return (
            f"{'\n\n'.join(blocks)}\n\n"
            "以上仅作背景，制度/流程以本轮检索资料为准，不要用记忆替代文档。"
        )

    async def remember_turn(
        self, user_id: str, session_id: str, question: str, answer: str
    ) -> None:
        if not self._api_key or self._classifier is None:
            return
        try:
            result = await self._classifier.chat.completions.create(
                model=self._classifier_model,
                temperature=0,
                max_tokens=120,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": CLASSIFIER_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"用户：{question}\n"
                            f"助手（仅供判断，不要当作用户事实）：{answer[:300]}"
                        ),
                    },
                ],
            )
            content = result.choices[0].message.content
            data = json.loads(content) if isinstance(content, str) else {}
        except Exception as err:
            logger.warning("Mem0 分类失败：%s", err)
            return

        written: list[str] = []
        add_opts = {
            "customInstructions": (
                "只用用户说的话抽取记忆，一句完整中文。"
                "只保存身份、岗位、偏好、约束，或用户声明的本轮任务。"
                "不要保存制度条文、流程、时限、负责人、系统名、引用编号。"
                "不要译成英文。"
            )
        }
        try:
            if data.get("write_user"):
                await self._mem0_add(user_id, None, question, add_opts)
                written.append("user")
            if data.get("write_session"):
                await self._mem0_add(user_id, session_id, question, add_opts)
                written.append("session")
            logger.info(
                "Mem0 分类：%s；写入=%s",
                str(data.get("reason") or ""),
                ",".join(written) or "无",
            )
        except Exception as err:
            logger.warning("Mem0 写入失败：%s", err)

    async def clear_session(self, user_id: str, session_id: str) -> None:
        if not self._api_key:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self._host}/api/v1/memories",
                    params={"user_id": user_id, "run_id": session_id},
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ):
                    pass
        except Exception as err:
            logger.warning("Mem0 会话层清理失败：%s", err)

    # ------------------------------------------------------------ 内部
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self._api_key}", "Content-Type": "application/json"}

    async def _mem0_search(self, filters: dict, query: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self._host}/api/v1/memories/search",
                params={"query": query, "top_k": self._top_k, "filters": json.dumps(filters)},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                body = await response.json()
                if response.status != 200:
                    raise RuntimeError(f"Mem0 search {response.status}")
                return body

    async def _mem0_add(
        self, user_id: str, run_id: str | None, text: str, opts: dict
    ) -> None:
        payload = {
            "messages": [{"role": "user", "content": text}],
            "user_id": user_id,
            **opts,
        }
        if run_id:
            payload["run_id"] = run_id
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._host}/api/v1/memories",
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Mem0 add {response.status}")
