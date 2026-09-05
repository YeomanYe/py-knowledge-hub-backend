"""KG 知识图谱构建（Neo4j 写入/查询/全景）。

图模型：
(KnowledgeDocument)-[:HAS_CHUNK]->(DocumentChunk)-[:MENTIONS]->(KnowledgeEntity)
(KnowledgeEntity)-[:RELATED_TO]->(KnowledgeEntity)
"""
from __future__ import annotations

import datetime
import logging

from neo4j import AsyncGraphDatabase

from ..config import get_settings
from .chunking import ChunkingService
from .extraction import ExtractionService
from .types import PipelineDocument

logger = logging.getLogger("graph-build")


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc).isoformat()
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value).astimezone(datetime.timezone.utc).isoformat()
        except ValueError:
            return value
    return str(value)


class GraphBuildService:
    def __init__(self, chunking_service: ChunkingService, extraction_service: ExtractionService) -> None:
        settings = get_settings()
        self._enabled = settings.neo4j_enabled
        self.driver = None
        if self._enabled:
            self.driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        self._chunking_service = chunking_service
        self._extraction_service = extraction_service

    async def close(self) -> None:
        if self.driver is not None:
            await self.driver.close()
            self.driver = None

    # ------------------------------------------------------------ 构建
    async def build_for_document(self, doc: PipelineDocument) -> int:
        if self.driver is None:
            logger.warning("跳过 KG 构建（Neo4j 不可用）：documentId=%s", doc.id)
            return 0
        if not (doc.content or "").strip():
            logger.info("文档内容为空，跳过 KG：documentId=%s", doc.id)
            return 0

        await self.delete_for_document(doc.id)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # ① 文档节点
        await self._run(
            """
            MERGE (d:KnowledgeDocument {id: $id})
            SET d.title = $title, d.summary = $summary, d.categoryId = $categoryId,
                d.authorId = $authorId, d.status = $status, d.tags = $tags,
                d.updatedAt = $now, d.createdAt = coalesce(d.createdAt, $now)
            """,
            id=doc.id,
            title=doc.title,
            summary=doc.summary or "",
            categoryId=doc.categoryId,
            authorId=doc.authorId,
            status=doc.status,
            tags=doc.tags or "",
            now=now,
        )

        # ② 分块（与 RAG 同款）
        chunks = await self._chunking_service.chunk(
            content=doc.content,
            document_id=doc.id,
            document_title=doc.title,
            category_id=doc.categoryId,
            author_id=doc.authorId,
            team_id=doc.teamId,
            doc_status=doc.status,
            publish_time=_iso(doc.publishTime),
        )

        total_entities = 0
        for chunk in chunks:
            # ③ chunk 节点 + 文档→块边
            await self._run(
                """
                MERGE (c:DocumentChunk {chunkId: $chunkId})
                SET c.documentId = $documentId, c.content = $content, c.heading = $heading,
                    c.chunkIndex = $chunkIndex, c.totalChunks = $totalChunks, c.updatedAt = $now
                WITH c
                MATCH (d:KnowledgeDocument {id: $documentId})
                MERGE (d)-[r:HAS_CHUNK]->(c)
                SET r.chunkIndex = $chunkIndex
                """,
                chunkId=chunk.chunkId,
                documentId=doc.id,
                content=chunk.content,
                heading=chunk.heading,
                chunkIndex=chunk.chunkIndex,
                totalChunks=chunk.totalChunks,
                now=now,
            )

            # ④ 抽取并落图；单块失败不阻断
            try:
                extracted = await self._extraction_service.extract(
                    chunk.content, chunk.heading, doc.title
                )
            except Exception as err:
                logger.error(
                    "KG 抽取失败，跳过该块：documentId=%s, chunk=%s, %s",
                    doc.id, chunk.chunkIndex, err,
                )
                extracted = _empty_extraction()
            extracted.chunkId = chunk.chunkId
            written = await self._write_extraction(extracted)
            total_entities += written

        logger.info(
            "KG 图谱构建完成：documentId=%s, chunks=%s, entities=%s",
            doc.id, len(chunks), total_entities,
        )
        return total_entities

    async def build_batch(self, docs: list[PipelineDocument]) -> None:
        for doc in docs:
            try:
                await self.build_for_document(doc)
            except Exception as err:
                logger.error("KG 构建失败：documentId=%s, %s", doc.id, err)

    # ------------------------------------------------------------ 删除
    async def delete_for_document(self, document_id: str) -> None:
        if self.driver is None:
            return
        await self._run(
            """
            MATCH (d:KnowledgeDocument {id: $id})
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:DocumentChunk)
            DETACH DELETE c, d
            """,
            id=document_id,
        )
        await self._run(
            """
            MATCH (e:KnowledgeEntity)
            WHERE NOT (e)<-[:MENTIONS]-()
            DETACH DELETE e
            """
        )
        logger.info("KG 图谱已删除：documentId=%s", document_id)

    # ------------------------------------------------------------ 查询
    async def list_nodes(self, type_: str | None = None, limit: int = 200) -> list[dict]:
        if self.driver is None:
            logger.warning("跳过图谱节点查询（Neo4j 不可用）")
            return []
        cap = max(1, min(limit, 500))
        records = await self._run(
            """
            MATCH (e:KnowledgeEntity)
            WHERE $type IS NULL OR $type = '' OR e.type = $type
            RETURN e.name AS id, e.name AS name, e.type AS type,
                   e.description AS description
            LIMIT $limit
            """,
            type=type_,
            limit=cap,
        )
        return [
            {
                "id": str(r.get("id") or ""),
                "name": str(r.get("name") or ""),
                "type": r.get("type"),
                "description": r.get("description"),
            }
            for r in records
        ]

    async def list_edges(self, limit: int = 500) -> list[dict]:
        if self.driver is None:
            logger.warning("跳过图谱边查询（Neo4j 不可用）")
            return []
        cap = max(1, min(limit, 1000))
        records = await self._run(
            """
            MATCH (a:KnowledgeEntity)-[r:RELATED_TO]->(b:KnowledgeEntity)
            RETURN a.name AS source, b.name AS target,
                   r.relation AS relation, r.weight AS weight
            LIMIT $limit
            """,
            limit=cap,
        )
        return [
            {
                "source": str(r.get("source") or ""),
                "target": str(r.get("target") or ""),
                "relation": r.get("relation") or "RELATED_TO",
                "weight": self._to_number(r.get("weight"), 0.5),
            }
            for r in records
        ]

    async def search_graph(self, keyword: str, limit: int = 50) -> list[dict]:
        if self.driver is None:
            logger.warning("跳过图谱检索（Neo4j 不可用）")
            return []
        kw = (keyword or "").strip()
        if not kw:
            return []
        cap = max(1, min(limit, 200))
        records = await self._run(
            """
            MATCH (n)
            WHERE toLower(coalesce(n.name, '')) CONTAINS toLower($kw)
               OR toLower(coalesce(n.title, '')) CONTAINS toLower($kw)
               OR toLower(coalesce(n.heading, '')) CONTAINS toLower($kw)
               OR toLower(coalesce(n.description, '')) CONTAINS toLower($kw)
               OR toLower(coalesce(n.summary, '')) CONTAINS toLower($kw)
               OR toLower(coalesce(n.content, '')) CONTAINS toLower($kw)
            RETURN labels(n)[0] AS label,
                   coalesce(n.name, n.title, n.heading, n.id, n.chunkId) AS name,
                   coalesce(n.id, n.chunkId, n.name) AS id,
                   n.type AS type,
                   n.title AS title,
                   n.description AS description,
                   n.heading AS heading,
                   n.documentId AS documentId,
                   n.summary AS summary,
                   CASE
                     WHEN n.content IS NULL THEN null
                     ELSE substring(n.content, 0, 160)
                   END AS snippet
            ORDER BY label, name
            LIMIT $limit
            """,
            kw=kw,
            limit=cap,
        )
        return [
            {
                "id": str(r.get("id") or ""),
                "name": str(r.get("name") or ""),
                "label": r.get("label"),
                "type": r.get("type"),
                "title": r.get("title"),
                "description": r.get("description"),
                "heading": r.get("heading"),
                "documentId": r.get("documentId"),
                "summary": r.get("summary"),
                "snippet": r.get("snippet"),
            }
            for r in records
        ]

    # ------------------------------------------------------------ 全景
    async def get_overview(
        self,
        keyword: str | None = None,
        entity_type: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        doc_limit: int = 24,
    ) -> dict:
        empty = {
            "nodes": [],
            "edges": [],
            "stats": {
                "nodeCount": 0,
                "edgeCount": 0,
                "documentCount": 0,
                "entityCount": 0,
                "tagCount": 0,
                "mentionCount": 0,
                "relatedCount": 0,
                "entityTypes": [],
            },
            "topEntities": [],
            "recentNodes": [],
            "entityTypes": [],
        }

        if self.driver is None:
            logger.warning("跳过图谱全景（Neo4j 不可用）")
            return empty

        kw = (keyword or "").strip()
        entity_type = (entity_type or "").strip() or None
        from_ = (from_ or "").strip() or None
        to = (to or "").strip() or None
        doc_limit = max(1, min(doc_limit or 24, 80))

        try:
            # 全库统计
            stats_records = await self._run(
                """
                OPTIONAL MATCH (d:KnowledgeDocument)
                WITH count(d) AS documentCount
                OPTIONAL MATCH (e:KnowledgeEntity)
                WITH documentCount, count(e) AS entityCount
                OPTIONAL MATCH ()-[rel:RELATED_TO]->()
                WITH documentCount, entityCount, count(rel) AS relatedCount
                OPTIONAL MATCH (:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e0:KnowledgeEntity)
                RETURN documentCount, entityCount, relatedCount, count(e0) AS mentionCount
                """
            )
            stats_row = stats_records[0] if stats_records else {}
            document_count = self._to_number(stats_row.get("documentCount"), 0)
            entity_count = self._to_number(stats_row.get("entityCount"), 0)
            related_count = self._to_number(stats_row.get("relatedCount"), 0)
            mention_count = self._to_number(stats_row.get("mentionCount"), 0)

            # 实体类型分布
            type_records = await self._run(
                """
                MATCH (e:KnowledgeEntity)
                WHERE e.type IS NOT NULL AND e.type <> ''
                RETURN e.type AS type, count(*) AS count
                ORDER BY count DESC
                """
            )
            entity_types = [
                {"type": str(r.get("type") or ""), "count": self._to_number(r.get("count"), 0)}
                for r in type_records
            ]

            # 被提及最多的 5 个实体
            top_records = await self._run(
                """
                MATCH (e:KnowledgeEntity)<-[:MENTIONS]-(:DocumentChunk)
                RETURN e.name AS name, e.type AS type, count(*) AS degree
                ORDER BY degree DESC
                LIMIT 5
                """
            )
            top_entities = [
                {
                    "name": str(r.get("name") or ""),
                    "type": r.get("type"),
                    "degree": self._to_number(r.get("degree"), 0),
                }
                for r in top_records
            ]

            # 最近 8 篇文档
            recent_records = await self._run(
                """
                MATCH (d:KnowledgeDocument)
                RETURN d.id AS id, d.title AS name, d.updatedAt AS updatedAt
                ORDER BY d.updatedAt DESC
                LIMIT 8
                """
            )
            recent_nodes = [
                {
                    "id": f"doc:{r.get('id') or ''}",
                    "name": str(r.get("name") or ""),
                    "kind": "document",
                    "updatedAt": r.get("updatedAt"),
                }
                for r in recent_records
            ]

            # 主查询：文档 + 提及实体
            doc_records = await self._run(
                """
                MATCH (d:KnowledgeDocument)
                WHERE ($kw = '' OR toLower(coalesce(d.title, '')) CONTAINS toLower($kw)
                      OR toLower(coalesce(d.summary, '')) CONTAINS toLower($kw)
                      OR toLower(coalesce(d.tags, '')) CONTAINS toLower($kw))
                  AND ($from IS NULL OR d.updatedAt >= $from)
                  AND ($to IS NULL OR d.updatedAt <= $to)
                WITH d ORDER BY d.updatedAt DESC LIMIT $docLimit
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e:KnowledgeEntity)
                WHERE $entityType IS NULL OR e.type = $entityType
                RETURN d.id AS docId, d.title AS docTitle, d.summary AS summary,
                       d.tags AS tags, d.updatedAt AS updatedAt,
                       collect(DISTINCT CASE WHEN e IS NULL THEN NULL ELSE {
                         name: e.name, type: e.type, description: e.description
                       } END) AS entities
                """,
                {
                    "kw": kw,
                    "entityType": entity_type,
                    "from": from_,
                    "to": to,
                    "docLimit": doc_limit,
                },
            )

            # 关键词命中实体时补文档
            if kw:
                extra_records = await self._run(
                    """
                    MATCH (e:KnowledgeEntity)<-[:MENTIONS]-(:DocumentChunk)<-[:HAS_CHUNK]-(d:KnowledgeDocument)
                    WHERE toLower(coalesce(e.name, '')) CONTAINS toLower($kw)
                       OR toLower(coalesce(e.description, '')) CONTAINS toLower($kw)
                    WITH DISTINCT d
                    WHERE ($from IS NULL OR d.updatedAt >= $from)
                      AND ($to IS NULL OR d.updatedAt <= $to)
                    OPTIONAL MATCH (d)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e2:KnowledgeEntity)
                    WHERE $entityType IS NULL OR e2.type = $entityType
                    RETURN d.id AS docId, d.title AS docTitle, d.summary AS summary,
                           d.tags AS tags, d.updatedAt AS updatedAt,
                           collect(DISTINCT CASE WHEN e2 IS NULL THEN NULL ELSE {
                             name: e2.name, type: e2.type, description: e2.description
                           } END) AS entities
                    LIMIT $docLimit
                    """,
                    {
                        "kw": kw,
                        "entityType": entity_type,
                        "from": from_,
                        "to": to,
                        "docLimit": doc_limit,
                    },
                )
                seen = {str(r.get("docId")) for r in doc_records}
                for record in extra_records:
                    if str(record.get("docId")) not in seen:
                        doc_records.append(record)

            node_map: dict[str, dict] = {}
            edge_map: dict[str, dict] = {}
            entity_names: set[str] = set()

            def add_edge(source: str, target: str, relation: str, kind: str) -> None:
                key = f"{kind}|{source}|{target}|{relation}"
                if key not in edge_map:
                    edge_map[key] = {"source": source, "target": target, "relation": relation, "kind": kind}

            def split_tags(raw) -> list[str]:
                return [t.strip() for t in str(raw or "").split(",") if t.strip()]

            for record in doc_records:
                doc_id = str(record.get("docId") or "")
                doc_node_id = f"doc:{doc_id}"
                node_map[doc_node_id] = {
                    "id": doc_node_id,
                    "name": str(record.get("docTitle") or ""),
                    "kind": "document",
                    "type": "DOCUMENT",
                    "documentId": doc_id,
                    "updatedAt": record.get("updatedAt"),
                    "description": record.get("summary"),
                }
                for tag in split_tags(record.get("tags")):
                    tag_id = f"tag:{tag}"
                    node_map[tag_id] = {"id": tag_id, "name": tag, "kind": "tag", "type": "TAG"}
                    add_edge(doc_node_id, tag_id, "标注", "tagged")

                for entity in record.get("entities") or []:
                    if not entity or not entity.get("name"):
                        continue
                    entity_id = f"entity:{entity['name']}"
                    entity_names.add(entity["name"])
                    node_map[entity_id] = {
                        "id": entity_id,
                        "name": entity["name"],
                        "kind": "entity",
                        "type": entity.get("type") or "CONCEPT",
                        "description": entity.get("description"),
                    }
                    add_edge(doc_node_id, entity_id, "提及", "mentions")

            if entity_names:
                related_records = await self._run(
                    """
                    MATCH (a:KnowledgeEntity)-[r:RELATED_TO]->(b:KnowledgeEntity)
                    WHERE a.name IN $names AND b.name IN $names
                    RETURN a.name AS source, b.name AS target,
                           r.relation AS relation, r.weight AS weight
                    LIMIT 400
                    """,
                    names=list(entity_names),
                )
                for record in related_records:
                    source = f"entity:{record.get('source') or ''}"
                    target = f"entity:{record.get('target') or ''}"
                    relation = record.get("relation") or "关联"
                    add_edge(source, target, relation, "related")

            nodes = list(node_map.values())
            edges = list(edge_map.values())
            tag_count = sum(1 for n in nodes if n["kind"] == "tag")

            return {
                "nodes": nodes,
                "edges": edges,
                "stats": {
                    "nodeCount": len(nodes),
                    "edgeCount": len(edges),
                    "documentCount": document_count,
                    "entityCount": entity_count,
                    "tagCount": tag_count,
                    "mentionCount": mention_count,
                    "relatedCount": related_count,
                    "entityTypes": entity_types,
                },
                "topEntities": top_entities,
                "recentNodes": recent_nodes,
                "entityTypes": [t["type"] for t in entity_types],
            }
        except Exception as err:
            logger.warning("图谱全景查询失败：%s", err)
            return empty

    # ------------------------------------------------------------ 内部
    def _to_number(self, value, fallback: float) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return fallback

    async def _write_extraction(self, result) -> int:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        count = 0

        for entity in result.entities:
            await self._run(
                """
                MERGE (e:KnowledgeEntity {name: $name})
                ON CREATE SET e.type = $type, e.description = $description,
                              e.aliases = $aliases, e.createdAt = $now, e.updatedAt = $now
                ON MATCH SET e.type = coalesce($type, e.type),
                             e.description = CASE WHEN $description <> '' THEN $description ELSE e.description END,
                             e.updatedAt = $now
                """,
                name=entity.name,
                type=entity.type,
                description=entity.description or "",
                aliases=entity.aliases or [],
                now=now,
            )
            count += 1

            if result.chunkId:
                await self._run(
                    """
                    MATCH (c:DocumentChunk {chunkId: $chunkId})
                    MATCH (e:KnowledgeEntity {name: $name})
                    MERGE (c)-[:MENTIONS]->(e)
                    """,
                    chunkId=result.chunkId,
                    name=entity.name,
                )

        for rel in result.relations:
            await self._run(
                """
                MATCH (a:KnowledgeEntity {name: $source})
                MATCH (b:KnowledgeEntity {name: $target})
                MERGE (a)-[r:RELATED_TO]->(b)
                ON CREATE SET r.relation = $relType, r.weight = $weight, r.createdAt = datetime()
                ON MATCH SET r.weight = coalesce($weight, r.weight)
                """,
                source=rel.source,
                target=rel.target,
                relType=rel.relation,
                weight=rel.weight if rel.weight is not None else 0.5,
            )

        return count

    async def _run(self, query: str, params: dict | None = None, **kwargs) -> list[dict]:
        if self.driver is None:
            return []
        merged = dict(params or {})
        merged.update(kwargs)
        async with self.driver.session() as session:
            result = await session.run(query, **merged)
            return await result.data()


def _empty_extraction():
    from .types import ExtractionResult

    return ExtractionResult(entities=[], relations=[])
