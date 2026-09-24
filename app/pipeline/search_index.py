"""文档级全文搜索索引（Elasticsearch kh_document）。

检索按当前用户可见范围过滤（公开 ∪ 自己写的 ∪ 所在团队）。
"""
from __future__ import annotations

import datetime
import logging

from elasticsearch import AsyncElasticsearch

from ..config import get_settings
from ..document_access import (
    ES_DOC_VISIBILITY_FIELDS,
    DocumentAccessScope,
    es_visibility_filter,
)

ES_INDEX = "kh_document"

logger = logging.getLogger("search-index")

_IK_TEXT = {
    "type": "text",
    "analyzer": "ik_max_word",
    "search_analyzer": "ik_smart",
}


class SearchIndexService:
    def __init__(self) -> None:
        settings = get_settings()
        self._es_enabled = settings.elasticsearch_enabled
        self._node = settings.elasticsearch_node
        self.es: AsyncElasticsearch | None = None
        if self._es_enabled:
            self.es = AsyncElasticsearch([self._node])

    async def close(self) -> None:
        if self.es is not None:
            await self.es.close()
            self.es = None

    async def index_document(self, doc: dict) -> None:
        if self.es is None:
            logger.warning("跳过搜索索引写入（ES 不可用）：documentId=%s", doc.get("id"))
            return

        await self._ensure_es_index()
        await self._ensure_visibility_mapping()

        doc_id = str(doc.get("id"))
        body = {**doc, "indexedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        await self.es.index(index=ES_INDEX, id=doc_id, document=body, refresh=True)
        logger.info("搜索索引已写入 ES：documentId=%s", doc_id)

    async def update_visibility(
        self,
        document_id: str,
        vis: dict,
    ) -> None:
        """已发布文档只改公开/团队时，补写可见性字段，不必整篇重索引。"""
        if self.es is None:
            logger.warning("跳过搜索可见性更新（ES 不可用）：documentId=%s", document_id)
            return
        try:
            await self.es.update(
                index=ES_INDEX,
                id=document_id,
                doc={
                    "isPublic": vis.get("isPublic", False),
                    "teamId": vis.get("teamId"),
                    "authorId": vis.get("authorId"),
                },
                refresh=True,
            )
            logger.info("搜索索引可见性已更新：documentId=%s", document_id)
        except Exception as err:
            logger.warning("搜索索引可见性更新失败：documentId=%s, %s", document_id, err)

    async def delete_document(self, document_id: str) -> None:
        if self.es is None:
            logger.warning("跳过搜索索引删除（ES 不可用）：documentId=%s", document_id)
            return
        try:
            await self.es.delete(index=ES_INDEX, id=document_id, refresh=True)
        except Exception as err:
            if "404" not in str(err):
                logger.warning("ES 删除失败：documentId=%s, %s", document_id, err)
        logger.info("搜索索引已删除：documentId=%s", document_id)

    async def search_documents(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 10,
        category_id: str | None = None,
        author_id: str | None = None,
        scope: DocumentAccessScope | None = None,
    ) -> dict:
        page = page or 1
        page_size = min(page_size or 10, 50)
        from_ = (page - 1) * page_size

        if self.es is None:
            logger.warning("跳过搜索查询（ES 不可用）")
            return {"items": [], "total": 0, "page": page, "pageSize": page_size}

        filters: list[dict] = []
        vis = es_visibility_filter(scope, ES_DOC_VISIBILITY_FIELDS) if scope else None
        if vis:
            filters.append(vis)
        if category_id:
            filters.append({"term": {"categoryId": category_id}})
        if author_id:
            filters.append({"term": {"authorId": author_id}})

        trimmed = (keyword or "").strip()
        if filters:
            query = {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": trimmed,
                                "fields": ["title^3", "summary^2", "content"],
                                "analyzer": "ik_smart",
                            }
                        }
                    ],
                    "filter": filters,
                }
            }
        else:
            query = {
                "multi_match": {
                    "query": trimmed,
                    "fields": ["title^3", "summary^2", "content"],
                    "analyzer": "ik_smart",
                }
            }

        try:
            response = await self.es.search(
                index=ES_INDEX,
                from_=from_,
                size=page_size,
                query=query,
                _source={"excludes": ["content"]},
                highlight={
                    "fields": {
                        "title": {"number_of_fragments": 0},
                        "content": {"fragment_size": 160, "number_of_fragments": 3},
                        "summary": {"fragment_size": 120, "number_of_fragments": 1},
                    }
                },
            )

            total_raw = response["hits"]["total"]
            total = total_raw if isinstance(total_raw, (int, float)) else (total_raw.get("value", 0) if total_raw else 0)

            items = []
            for hit in response["hits"]["hits"]:
                src = hit.get("_source") or {}
                highlight = hit.get("highlight") or {}
                items.append(
                    {
                        "id": str(src.get("id") or hit.get("_id") or ""),
                        "title": src.get("title") or "",
                        "summary": src.get("summary"),
                        "categoryId": src.get("categoryId"),
                        "tags": src.get("tags"),
                        "authorId": src.get("authorId"),
                        "teamId": src.get("teamId"),
                        "isPublic": src.get("isPublic"),
                        "status": src.get("status"),
                        "publishTime": src.get("publishTime"),
                        "score": hit.get("_score") or 0,
                        "highlight": {
                            "title": highlight.get("title", []),
                            "summary": highlight.get("summary", []),
                            "content": highlight.get("content", []),
                        },
                    }
                )

            return {"items": items, "total": total, "page": page, "pageSize": page_size}
        except Exception as err:
            logger.warning("搜索查询失败：%s", err)
            return {"items": [], "total": 0, "page": page, "pageSize": page_size}

    async def _ensure_es_index(self) -> None:
        if self.es is None:
            return
        exists = await self.es.indices.exists(index=ES_INDEX)
        if exists:
            return
        await self.es.indices.create(
            index=ES_INDEX,
            mappings={
                "properties": {
                    "id": {"type": "keyword"},
                    "title": _IK_TEXT,
                    "summary": _IK_TEXT,
                    "content": _IK_TEXT,
                    "tags": {"type": "keyword"},
                    "status": {"type": "integer"},
                    "categoryId": {"type": "keyword"},
                    "authorId": {"type": "keyword"},
                    "teamId": {"type": "keyword"},
                    "isPublic": {"type": "boolean"},
                    "publishTime": {"type": "date"},
                }
            },
        )
        logger.info("已创建 ES 索引：%s", ES_INDEX)

    async def _ensure_visibility_mapping(self) -> None:
        """已有索引补可见性字段（旧 mapping 没有 isPublic）。"""
        if self.es is None:
            return
        try:
            await self.es.indices.put_mapping(
                index=ES_INDEX,
                properties={
                    "isPublic": {"type": "boolean"},
                    "teamId": {"type": "keyword"},
                    "authorId": {"type": "keyword"},
                },
            )
        except Exception as err:
            logger.warning("kh_document 可见性 mapping 更新失败：%s", err)
