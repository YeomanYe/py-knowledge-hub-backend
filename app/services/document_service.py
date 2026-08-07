"""文档服务。"""
from __future__ import annotations

import datetime
import re

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common import next_snowflake_id
from ..database import get_mongo
from ..models import Document, DocumentStatus
from ..mongo_models import DocumentContentRepo, new_content_id

logger = None


def _to_dict(doc: Document, content: str | None = None) -> dict:
    result = {
        "id": doc.id,
        "title": doc.title,
        "contentId": doc.content_id,
        "summary": doc.summary,
        "categoryId": doc.category_id,
        "teamId": doc.team_id,
        "authorId": doc.author_id,
        "coverImage": doc.cover_image,
        "tags": doc.tags,
        "status": doc.status,
        "remark": doc.remark,
        "viewCount": doc.view_count,
        "likeCount": doc.like_count,
        "commentCount": doc.comment_count,
        "favouriteCount": doc.favourite_count,
        "wordCount": doc.word_count,
        "publishTime": doc.publish_time,
        "isPublic": doc.is_public,
        "createdAt": doc.created_at,
        "updatedAt": doc.updated_at,
        "createBy": doc.create_by,
        "updateBy": doc.update_by,
    }
    if content is not None:
        result["content"] = content
    return result


class DocumentService:
    def __init__(self, content_repo: DocumentContentRepo | None = None) -> None:
        self._content_repo = content_repo or DocumentContentRepo(get_mongo())

    async def create(self, session: AsyncSession, dto) -> dict:
        requested_status = dto.status if dto.status is not None else DocumentStatus.Draft
        if requested_status not in (DocumentStatus.Draft, DocumentStatus.Published):
            raise HTTPException(400, "创建文档仅允许草稿或已发布状态")

        doc_id = next_snowflake_id()
        word_count = self.count_words(dto.content)
        content_summary = dto.summary or self.build_content_summary(dto.content)

        content_id = await self._content_repo.create(
            content_id=new_content_id(),
            document_id=doc_id,
            content=dto.content,
            content_length=len(dto.content),
            content_summary=content_summary,
        )

        try:
            doc = Document(
                id=doc_id,
                title=dto.title,
                content_id=content_id,
                summary=dto.summary,
                category_id=dto.categoryId,
                team_id=dto.teamId,
                author_id=dto.authorId,
                cover_image=dto.coverImage,
                tags=dto.tags,
                status=requested_status,
                remark=dto.remark,
                is_public=bool(dto.isPublic) if dto.isPublic is not None else False,
                word_count=word_count,
                publish_time=(
                    datetime.datetime.now()
                    if requested_status == DocumentStatus.Published
                    else None
                ),
                create_by=dto.authorId,
                update_by=dto.authorId,
            )
            session.add(doc)
            await session.flush()
            return {**_to_dict(doc), "content": dto.content}
        except Exception:
            await self._content_repo.hard_delete_by_document_id(doc_id)
            raise

    async def find_all(self, session: AsyncSession, query) -> dict:
        page = query.page or 1
        page_size = query.pageSize or 20
        stmt = select(Document).where(Document.deleted.is_(False))
        total_stmt = select(func.count(Document.id)).where(Document.deleted.is_(False))

        if query.title:
            cond = Document.title.ilike(f"%{query.title}%")
            stmt = stmt.where(cond)
            total_stmt = total_stmt.where(cond)
        if query.categoryId:
            stmt = stmt.where(Document.category_id == query.categoryId)
            total_stmt = total_stmt.where(Document.category_id == query.categoryId)
        if query.teamId:
            stmt = stmt.where(Document.team_id == query.teamId)
            total_stmt = total_stmt.where(Document.team_id == query.teamId)
        if query.authorId:
            stmt = stmt.where(Document.author_id == query.authorId)
            total_stmt = total_stmt.where(Document.author_id == query.authorId)
        if query.status is not None:
            stmt = stmt.where(Document.status == query.status)
            total_stmt = total_stmt.where(Document.status == query.status)

        stmt = (
            stmt.order_by(Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [_to_dict(d) for d in (await session.execute(stmt)).scalars().all()]
        total = (await session.execute(total_stmt)).scalar() or 0
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    async def find_one(self, session: AsyncSession, doc_id: str, with_content: bool = True) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        if not with_content:
            return _to_dict(doc)
        content = await self._load_content(doc.content_id)
        return {**_to_dict(doc), "content": content}

    async def update(self, session: AsyncSession, doc_id: str, dto) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        if doc.status == DocumentStatus.PendingReview:
            if dto.content is not None or dto.title is not None:
                raise HTTPException(400, "审核中的文档不可编辑")

        if dto.title is not None:
            doc.title = dto.title
        if dto.summary is not None:
            doc.summary = dto.summary
        if dto.categoryId is not None:
            doc.category_id = dto.categoryId
        if dto.teamId is not None:
            doc.team_id = dto.teamId
        if dto.coverImage is not None:
            doc.cover_image = dto.coverImage
        if dto.tags is not None:
            doc.tags = dto.tags
        if dto.remark is not None:
            doc.remark = dto.remark
        if dto.isPublic is not None:
            doc.is_public = dto.isPublic
        if dto.authorId is not None:
            doc.update_by = dto.authorId

        if dto.content is not None:
            content_summary = dto.summary or self.build_content_summary(dto.content)
            updated = await self._content_repo.update_content(
                doc.content_id, dto.content, len(dto.content), content_summary
            )
            if not updated:
                raise HTTPException(400, f"Document content {doc.content_id} not found")
            doc.word_count = self.count_words(dto.content)

        await session.flush()
        final_content = dto.content if dto.content is not None else await self._load_content(doc.content_id)
        return {**_to_dict(doc), "content": final_content}

    async def remove(self, session: AsyncSession, doc_id: str) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        doc.deleted = True
        await session.flush()
        await self._content_repo.mark_deleted_by_document_id(doc_id)
        return {"id": doc_id, "deleted": True}

    async def _load_content(self, content_id: str) -> str:
        content_doc = await self._content_repo.find_by_id(content_id)
        return (content_doc or {}).get("content", "")

    async def _find_by_id_or_throw(self, session: AsyncSession, doc_id: str) -> Document:
        stmt = select(Document).where(Document.id == doc_id, Document.deleted.is_(False))
        doc = (await session.execute(stmt)).scalars().first()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found")
        return doc

    def build_content_summary(self, content: str, max_len: int = 200) -> str:
        trimmed = re.sub(r"\s+", " ", content.strip())
        return trimmed if len(trimmed) <= max_len else f"{trimmed[:max_len]}..."

    def count_words(self, content: str) -> int:
        trimmed = content.strip()
        if not trimmed:
            return 0
        cjk = len(re.findall(r"[\u4e00-\u9fff]", trimmed))
        latin = len(re.sub(r"[\u4e00-\u9fff]", " ", trimmed).strip().split())
        return cjk + latin
