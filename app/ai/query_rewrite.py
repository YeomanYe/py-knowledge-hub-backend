"""意图路由 + 检索评估 + 不足改写（OpenAI 兼容口 + JSON 结构化输出）。

失败时全部按「知识库」兜底：宁可多检索也不漏内部制度。
"""
from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from ..config import get_settings
from .chat_memory_util import compact_rewrite_context

logger = logging.getLogger("query-rewrite")

CHAT_INTENTS = ("chitchat", "profile", "kb", "web", "kb_then_web")

INTENT_LABEL = {
    "chitchat": "闲聊",
    "profile": "个人偏好",
    "kb": "知识库",
    "web": "联网搜索",
    "kb_then_web": "知识库，不足则联网",
}

ROUTE_PROMPT = (
    "你是企业知识库的意图与检索改写器。根据对话判断本轮意图，给出文档检索句，以及图谱要用的短实体名。\n"
    "\n"
    "## intent\n"
    "- chitchat：问好、致谢、与库无关的闲聊\n"
    "- profile：只问/改「我自己」的名字、我所在部门、回答长短偏好。主语必须是用户本人\n"
    "- kb：公司制度、流程、岗位、内部系统、文档\n"
    "- web：只要最新公开信息，明显不是内部制度\n"
    "- kb_then_web：像内部问题但也可能要外部时效（政策新闻+内部流程）\n"
    "\n"
    "## 易混\n"
    "- 「我是哪个部门 / 以后回答短一点」→ profile\n"
    "- 「预算审核员属于哪个部门」→ kb，不要因为出现「部门」就判 profile\n"
    "- 记忆里的用户部门不能改变本题意图\n"
    "\n"
    "## standalone_query\n"
    "- 给知识库全文检索用，可以是一句；消解「这个 / 谁负责 / 怎么办」，尽量不超过 40 字\n"
    "- 不要编造上文没有的专有名词、条款号\n"
    "- 不要复述助手已给出的制度条文\n"
    "- 闲聊/偏好：用原问题即可\n"
    "\n"
    "## graph_queries\n"
    "- 只在 kb / kb_then_web 填写，其他意图必须空数组\n"
    "- 每个词 2～4 个字的实体短名（人/岗/制度对象），不要整句、不要问号\n"
    "- 必须拆开：发票如何报销 → 发票、报销；发票报销流程和要求 → 发票、报销\n"
    "- 可补 1 个同域短名（报销可带差旅）\n"
    "- 禁止：流程、要求、办法、如何、怎么、什么、是什么\n"
    "\n"
    "只输出 JSON：{\"intent\": \"...\", \"standalone_query\": \"...\", \"graph_queries\": [...]}"
)

GRADE_PROMPT = (
    "你是企业知识库的检索评估器。判断命中资料能否回答用户问题。\n"
    "\n"
    "- 切题：同一主题、同一制度/岗位/流程，能支撑作答（不必覆盖每个细节）\n"
    "- 不切题：只是词沾边（如问加班餐补却命中差旅报销）、或完全另一件事\n"
    "- 只根据给定标题和摘录判断，不要假设库里还有别的文档\n"
    "\n"
    "只输出 JSON：{\"relevant\": true 或 false, \"reason\": \"一句中文\"}"
)

RETRY_REWRITE_PROMPT = (
    "你是企业知识库的检索改写器。上次检索不足，请换一种问法再查。\n"
    "\n"
    "- 紧扣用户原问题，不要跑题\n"
    "- 不要重复上次检索词\n"
    "- 可换同义、补全制度/岗位/补贴类型等核心实体\n"
    "- 不要编造条款号、专有名词\n"
    "- 一句中文，尽量不超过 40 字\n"
    "\n"
    "只输出 JSON：{\"query\": \"...\"}"
)


def summarize_hits(hits: list, limit: int = 5) -> str:
    lines = []
    for i, hit in enumerate(hits[:limit]):
        heading = f" / {hit.heading}" if hit.heading else ""
        snippet = " ".join(hit.content.split()).strip()[:180]
        score = f"{hit.score:.2f}" if hit.score is not None and abs(hit.score) < 1e9 else "-"
        lines.append(f"{i + 1}. {hit.documentTitle}{heading}（分={score}）\n{snippet}")
    return "\n".join(lines)


def distinct_query(next_query: str, previous: str) -> str:
    query = " ".join(next_query.split()).strip()
    if not query:
        return ""
    if query == " ".join(previous.split()).strip():
        return ""
    return query


class ChatQueryRewriteService:
    def __init__(self) -> None:
        settings = get_settings()
        self._timeout_ms = settings.ai_query_rewrite_timeout_ms
        self._client: AsyncOpenAI | None = None

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
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=self._timeout_ms / 1000
        )

    # ------------------------------------------------------------ 意图路由
    async def classify(
        self, question: str, history: list[dict]
    ) -> dict:
        """返回 {intent,label,query,graphQueries,allowRetrieve,allowGraph,allowWeb}。"""
        fallback = self._to_plan("kb", question)
        if self._client is None:
            return fallback

        context = compact_rewrite_context(history)
        try:
            result = await self._structured(
                ROUTE_PROMPT,
                (
                    f"对话：\n{context}\n\n当前问题：{question}"
                    if context
                    else f"当前问题：{question}"
                ),
            )
            intent = result.get("intent") if result.get("intent") in CHAT_INTENTS else "kb"
            query = str(result.get("standalone_query") or "").strip() or question
            graph_queries = result.get("graph_queries") or []
            plan = self._to_plan(intent, query, graph_queries)
            logger.info(
                "意图：%s query=%s graph=%s",
                plan["intent"],
                plan["query"][:80],
                "/".join(plan["graphQueries"]) or "-",
            )
            return plan
        except Exception as err:
            logger.warning("意图识别失败，按知识库处理：%s", err)
            return fallback

    async def rewrite(self, question: str, history: list[dict]) -> dict:
        plan = await self.classify(question, history)
        return {"query": plan["query"], "needRetrieve": plan["allowRetrieve"]}

    # ------------------------------------------------------------ 检索评估
    async def grade_hits(self, question: str, hits: list) -> dict:
        """{ok, reason, text}；空结果不调模型；评估失败时有召回则放行。"""
        if not hits:
            return {"ok": False, "reason": "empty", "text": "未检索到资料"}
        if self._client is None:
            return {"ok": True, "reason": "relevant", "text": "有召回（未配置评估模型）"}
        try:
            result = await self._structured(
                GRADE_PROMPT,
                f"用户问题：{question}\n\n命中资料：\n{summarize_hits(hits)}",
            )
            ok = bool(result.get("relevant"))
            text = str(result.get("reason") or "").strip() or ("资料切题" if ok else "资料不切题")
            logger.info("检索评估：%s %s", "切题" if ok else "不切题", text[:80])
            return {"ok": ok, "reason": "relevant" if ok else "irrelevant", "text": text}
        except Exception as err:
            logger.warning("检索评估失败，按有召回放行：%s", err)
            return {"ok": True, "reason": "relevant", "text": "评估失败，按有召回处理"}

    # ------------------------------------------------------------ 不足改写
    async def rewrite_after_retrieve(
        self,
        question: str,
        previous_query: str,
        grade: dict,
        hits: list,
    ) -> str:
        fallback = distinct_query(question, previous_query)
        if self._client is None:
            return fallback
        try:
            hit_block = (
                f"\n上次命中（不切题）：\n{summarize_hits(hits, 3)}" if hits else "\n上次无命中。"
            )
            result = await self._structured(
                RETRY_REWRITE_PROMPT,
                f"用户问题：{question}\n上次检索词：{previous_query}\n不足原因：{grade['text']}{hit_block}",
            )
            query = distinct_query(str(result.get("query") or ""), previous_query)
            if query:
                logger.info("检索改写：%s → %s", previous_query[:40], query[:40])
                return query
            return fallback
        except Exception as err:
            logger.warning("检索改写失败，回退原问题：%s", err)
            return fallback

    # ------------------------------------------------------------ 内部
    async def _structured(self, system: str, user: str) -> dict:
        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content
        data = json.loads(content) if isinstance(content, str) else {}
        if not isinstance(data, dict):
            raise ValueError("结构化输出不是对象")
        return data

    def _to_plan(
        self, intent: str, query: str, graph_queries: list | None = None
    ) -> dict:
        kb = intent in ("kb", "kb_then_web")
        return {
            "intent": intent,
            "label": INTENT_LABEL.get(intent, "知识库"),
            "query": query,
            "graphQueries": (
                [str(q).strip() for q in (graph_queries or []) if str(q).strip()]
                if kb
                else []
            ),
            "allowRetrieve": kb,
            "allowGraph": kb,
            "allowWeb": intent in ("web", "kb_then_web"),
        }
