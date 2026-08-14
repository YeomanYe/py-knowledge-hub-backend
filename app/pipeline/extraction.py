"""实体/关系抽取服务（ChatOpenAI.withStructuredOutput 等价）。

用 OpenAI 兼容 Chat Completions + JSON mode 拿到结构化结果，再按 zod 等价规则
截断数量、规范化类型、丢弃挂空实体的关系。
"""
from __future__ import annotations

import json
import logging
import time

from openai import AsyncOpenAI

from ..config import get_settings
from .kg_schema import (
    build_extraction_system_prompt,
    normalize_entity_type,
    normalize_relation_type,
)
from .types import ExtractionResult, ExtractedEntity, ExtractedRelation

logger = logging.getLogger("extraction")


class ExtractionService:
    def __init__(self) -> None:
        settings = get_settings()
        self._max_entities = settings.kg_max_entities
        self._max_relations = settings.kg_max_relations
        self._timeout_ms = settings.kg_llm_timeout_ms

        api_key = (
            settings.openai_api_key
            or settings.llm_api_key
            or settings.dashscope_api_key
        )
        if not api_key:
            self._client = None
            return

        base_url = settings.openai_base_url or settings.llm_base_url or (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self._model = settings.model_name or settings.llm_model or "qwen-plus"
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def extract(
        self, content: str, heading: str | None, document_title: str
    ) -> ExtractionResult:
        if not (content or "").strip():
            return ExtractionResult(entities=[], relations=[])
        return await self._extract_by_llm(content, heading, document_title)

    async def _extract_by_llm(
        self, content: str, heading: str | None, document_title: str
    ) -> ExtractionResult:
        if self._client is None:
            raise RuntimeError(
                "KG 抽取未配置 API Key（OPENAI_API_KEY / LLM_API_KEY / DASHSCOPE_API_KEY）"
            )

        system = build_extraction_system_prompt(self._max_entities, self._max_relations)
        user = f"文档标题: {document_title}\n章节: {heading or '无'}\n\n内容:\n{content[:4000]}"

        started = time.time()
        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed: dict = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # 兼容模型偶尔输出的 ```json 包裹
                cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
                try:
                    parsed = json.loads(cleaned)
                except json.JSONDecodeError:
                    logger.warning("KG 抽取 JSON 解析失败，返回空结果")
                    parsed = {}

        logger.info(
            "KG 抽取完成：title=%s, elapsed=%sms, chars=%s, entities=%s",
            document_title,
            int((time.time() - started) * 1000),
            len(content),
            len(parsed.get("entities") or []),
        )
        return self._to_extraction_result(parsed)

    def _to_extraction_result(self, parsed: dict) -> ExtractionResult:
        entity_names: set[str] = set()
        entities: list[ExtractedEntity] = []
        for e in (parsed.get("entities") or [])[: self._max_entities]:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            entity_names.add(name)
            aliases = [
                str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()
            ]
            entities.append(
                ExtractedEntity(
                    name=name,
                    type=normalize_entity_type(e.get("type")),
                    description=str(e.get("description") or "").strip(),
                    aliases=aliases,
                )
            )

        relations: list[ExtractedRelation] = []
        for r in (parsed.get("relations") or [])[: self._max_relations]:
            if not isinstance(r, dict):
                continue
            source = str(r.get("source") or "").strip()
            target = str(r.get("target") or "").strip()
            if not source or not target or source not in entity_names or target not in entity_names:
                continue
            weight = r.get("weight")
            relations.append(
                ExtractedRelation(
                    source=source,
                    target=target,
                    relation=normalize_relation_type(r.get("relation") or r.get("type")),
                    weight=weight if isinstance(weight, (int, float)) else 0.5,
                )
            )

        return ExtractionResult(entities=entities, relations=relations)
