"""文档服务（CRUD/发布/归档/上传解析/词数统计）。"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common import next_snowflake_id
from ..document_access import (
    can_read_document,
    can_write_document,
    access_from_user,
)
from ..models import Document, DocumentStatus
from ..mongo_models import DocumentContentRepo, new_content_id
from ..storage import RustfsService
from .document_review_service import DocumentReviewService
from ..parsers.file_parser import FileParserService
from ..parsers.markdown_util import (
    decode_upload_filename,
    get_extension,
    title_from_filename,
)

logger = logging.getLogger("document")


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
    def __init__(
        self,
        content_repo: DocumentContentRepo,
        file_parser_service: FileParserService,
        rustfs: RustfsService,
        pipeline_publisher,
        review_service: DocumentReviewService,
        orchestrator=None,
    ) -> None:
        self._content_repo = content_repo
        self._file_parser_service = file_parser_service
        self._rustfs = rustfs
        self._pipeline_publisher = pipeline_publisher
        self._review_service = review_service
        self._orchestrator = orchestrator

    # ------------------------------------------------------------ 创建
    async def create(self, session: AsyncSession, dto, actor: dict) -> dict:
        requested_status = dto.status if dto.status is not None else DocumentStatus.Draft
        if requested_status not in (DocumentStatus.Draft, DocumentStatus.Published):
            raise HTTPException(400, "创建文档仅允许草稿或已发布状态")
        if (
            requested_status == DocumentStatus.Published
            and self._review_service.is_require_approval()
        ):
            raise HTTPException(400, "开启审核时请先创建草稿，再提交发布/审核")

        doc_id = next_snowflake_id()
        word_count = self.count_words(dto.content)
        status = requested_status
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
                author_id=actor["userId"],
                cover_image=dto.coverImage,
                tags=dto.tags,
                status=status,
                remark=dto.remark,
                is_public=bool(dto.isPublic) if dto.isPublic is not None else False,
                word_count=word_count,
                publish_time=(
                    datetime.datetime.now()
                    if status == DocumentStatus.Published
                    else None
                ),
                create_by=actor["userId"],
                update_by=actor["userId"],
            )
            session.add(doc)
            await session.flush()

            if status == DocumentStatus.Published:
                await self.safe_publish(doc)

            return {**_to_dict(doc), "content": dto.content}
        except Exception:
            await self._content_repo.hard_delete_by_document_id(doc_id)
            raise

    # ------------------------------------------------------------ 查询
    async def find_all(self, session: AsyncSession, query, user: dict | None = None) -> dict:
        page = query.page or 1
        page_size = query.pageSize or 20
        stmt = select(Document).where(Document.deleted.is_(False))
        total_stmt = select(func.count(Document.id)).where(Document.deleted.is_(False))

        # 非超管：公开 ∪ 自己写的 ∪ 所在团队（团队文档须已发布才可见）
        scope = access_from_user(user)
        if not scope.unrestricted:
            if scope.teamIds:
                vis_cond = or_(
                    Document.author_id == scope.userId,
                    and_(
                        Document.status == DocumentStatus.Published,
                        or_(
                            Document.is_public.is_(True),
                            Document.team_id.in_(scope.teamIds),
                        ),
                    ),
                )
            else:
                vis_cond = or_(
                    Document.author_id == scope.userId,
                    and_(
                        Document.status == DocumentStatus.Published,
                        Document.is_public.is_(True),
                    ),
                )
            stmt = stmt.where(vis_cond)
            total_stmt = total_stmt.where(vis_cond)

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

    async def find_one(
        self,
        session: AsyncSession,
        doc_id: str,
        with_content: bool = True,
        user: dict | None = None,
    ) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        if user is not None and not can_read_document(doc, access_from_user(user)):
            raise HTTPException(403, "无权查看该文档")
        if not with_content:
            return _to_dict(doc)
        content = await self._load_content(doc.content_id)
        return {**_to_dict(doc), "content": content}

    # ------------------------------------------------------------ 更新
    async def update(self, session: AsyncSession, doc_id: str, dto, actor: dict) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        self._assert_writable(doc, actor)
        old_status = doc.status
        old_is_public = bool(doc.is_public)
        old_team_id = doc.team_id or None

        if doc.status == DocumentStatus.PendingReview:
            if dto.content is not None or dto.title is not None:
                raise HTTPException(400, "审核中的文档不可编辑")
        elif not self._can_edit_content(doc.status):
            raise HTTPException(400, "当前文档状态不允许编辑")

        if dto.status is not None and dto.status != doc.status:
            if dto.status == DocumentStatus.Draft and doc.status == DocumentStatus.Published:
                doc.status = DocumentStatus.Draft
            else:
                raise HTTPException(
                    400, "请使用 publish / archive / save-draft / 审核接口变更文档状态"
                )

        content_changed = False
        new_content: str | None = None

        if dto.content is not None:
            content_changed = True
            new_content = dto.content
            content_summary = dto.summary or self.build_content_summary(dto.content)
            updated = await self._content_repo.update_content(
                doc.content_id, dto.content, len(dto.content), content_summary
            )
            if not updated:
                raise HTTPException(400, f"Document content {doc.content_id} not found")
            doc.word_count = self.count_words(dto.content)
        elif dto.summary is not None:
            await self._content_repo.update_content(
                doc.content_id,
                (await self._load_content(doc.content_id)),
                len(await self._load_content(doc.content_id)),
                dto.summary,
            )

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
        doc.update_by = actor["userId"]

        await session.flush()
        final_content = new_content if new_content is not None else await self._load_content(doc.content_id)

        visibility_changed = bool(doc.is_public) != old_is_public or (doc.team_id or None) != old_team_id
        await self._sync_pipeline_after_update(
            doc, old_status, doc.status, content_changed, visibility_changed
        )

        return {**_to_dict(doc), "content": final_content}

    # ------------------------------------------------------------ 状态流转
    async def publish(self, session: AsyncSession, doc_id: str, actor: dict) -> dict:
        logger.info("发布文档：documentId=%s", doc_id)
        doc = await self._find_by_id_or_throw(session, doc_id)
        self._assert_writable(doc, actor)

        if not self._can_publish_from(doc.status):
            raise HTTPException(400, "当前文档状态不允许发布")
        if doc.status == DocumentStatus.PendingReview:
            raise HTTPException(400, "文档审核中，请等待审核结果")

        if self._review_service.is_require_approval():
            if doc.status in (DocumentStatus.Draft, DocumentStatus.Published):
                saved = await self._review_service.submit_for_review(session, doc_id, actor)
                content = await self._load_content(saved.content_id)
                return {**_to_dict(saved), "content": content}

        return await self.direct_publish(session, doc_id, actor)

    async def direct_publish(self, session: AsyncSession, doc_id: str, actor: dict | None = None) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        if actor is not None:
            self._assert_writable(doc, actor)
        if doc.status not in (
            DocumentStatus.Draft,
            DocumentStatus.Published,
            DocumentStatus.Archived,
            DocumentStatus.PendingReview,
        ):
            raise HTTPException(400, "当前文档状态不允许发布")

        import datetime

        doc.status = DocumentStatus.Published
        doc.publish_time = datetime.datetime.now()
        if actor and actor.get("userId"):
            doc.update_by = actor["userId"]
        await session.flush()
        content = await self._load_content(doc.content_id)
        await self.safe_publish(doc)

        logger.info("文档发布成功：documentId=%s", doc_id)
        return {**_to_dict(doc), "content": content}

    async def archive(self, session: AsyncSession, doc_id: str, actor: dict) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        self._assert_writable(doc, actor)
        if not self._can_archive(doc.status):
            raise HTTPException(400, "只有已发布文档可以归档")

        doc.status = DocumentStatus.Archived
        doc.update_by = actor["userId"]
        await session.flush()
        await self.safe_unpublish(doc_id)

        logger.info("文档已归档：documentId=%s", doc_id)
        return _to_dict(doc)

    async def save_as_draft(self, session: AsyncSession, doc_id: str, actor: dict) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        self._assert_writable(doc, actor)
        if doc.status != DocumentStatus.Published:
            raise HTTPException(400, "只有已发布文档可以保存为草稿")

        doc.status = DocumentStatus.Draft
        doc.update_by = actor["userId"]
        await session.flush()
        await self.safe_unpublish(doc_id)

        logger.info("文档已保存为草稿：documentId=%s", doc_id)
        return _to_dict(doc)

    async def remove(self, session: AsyncSession, doc_id: str, actor: dict) -> dict:
        doc = await self._find_by_id_or_throw(session, doc_id)
        self._assert_writable(doc, actor)

        if doc.status == DocumentStatus.Published:
            await self.safe_unpublish(doc_id)

        doc.deleted = True
        doc.update_by = actor["userId"]
        await session.flush()
        await self._content_repo.mark_deleted_by_document_id(doc_id)

        return {"id": doc_id, "deleted": True}

    # ------------------------------------------------------------ 上传解析
    async def upload_and_create_document(
        self, session: AsyncSession, file, meta, actor: dict
    ) -> dict:
        if file is None or not file.file or not getattr(file, "filename", None):
            raise HTTPException(400, "请上传文件（form-data 字段名: file）")

        data = file.file.read()
        if not data:
            raise HTTPException(400, "文件不能为空")

        original_filename = decode_upload_filename(file.filename)
        extension = get_extension(original_filename)

        if not self._file_parser_service.is_supported(extension):
            raise HTTPException(
                400,
                f"不支持的文件格式: {extension}，支持的格式: {self._file_parser_service.supported_list()}",
            )

        logger.info(
            "上传并解析文件：name=%s, size=%s, ext=%s", original_filename, len(data), extension
        )

        try:
            parsed_content = await self._file_parser_service.parse(
                originalname=original_filename, buffer=data, size=len(data)
            )
        except HTTPException:
            raise
        except Exception as err:
            logger.error("文件解析失败：name=%s, error=%s", original_filename, err)
            raise HTTPException(400, f"文件解析失败: {err}")

        file_url: str | None = None
        if self._rustfs.is_enabled():
            try:
                file_url = await self._rustfs.upload_bytes(
                    data,
                    file_name=original_filename,
                    content_type=file.content_type or "application/octet-stream",
                    prefix="documents",
                )
            except Exception as err:
                logger.error("原文件上传 RustFS 失败：%s", err)
                raise HTTPException(400, f"原文件上传失败: {err}")
        else:
            logger.warning("RustFS 未启用，跳过原文件上传")

        title = title_from_filename(original_filename)

        created = await self.create(session, _UploadMeta.from_parse(title, parsed_content, meta), actor)

        preview_len = min(200, len(parsed_content))
        result = {
            "documentId": created["id"],
            "title": title,
            "fileUrl": file_url,
            "fileSize": len(data),
            "fileExtension": extension,
            "contentLength": len(parsed_content),
            "contentPreview": parsed_content[:preview_len],
            "status": DocumentStatus.Draft,
        }

        logger.info(
            "文件解析并创建文档成功：documentId=%s, title=%s, ext=%s, chars=%s, fileUrl=%s",
            created["id"], title, extension, len(parsed_content), file_url,
        )
        return result

    # ------------------------------------------------------------ 内部
    async def _sync_pipeline_after_update(
        self,
        doc: Document,
        old_status: int,
        new_status: int,
        content_changed: bool,
        visibility_changed: bool = False,
    ) -> None:
        was_published = old_status == DocumentStatus.Published
        is_published = new_status == DocumentStatus.Published

        if was_published and not is_published:
            await self.safe_unpublish(doc.id)
            return

        if is_published and content_changed:
            if not self._review_service.is_require_approval():
                await self.safe_publish(doc)
            return

        # 已发布文档只改公开/团队：三端（向量/搜索/图谱）同步可见性，不必重建
        if is_published and visibility_changed and self._orchestrator is not None:
            try:
                await self._orchestrator.update_visibility(doc.id)
            except Exception as err:
                logger.warning(
                    "可见性同步失败（不影响文档保存）：documentId=%s, %s", doc.id, err
                )

    async def _load_content(self, content_id: str) -> str:
        content_doc = await self._content_repo.find_by_id(content_id)
        return (content_doc or {}).get("content", "")

    async def safe_publish(self, doc: Document) -> None:
        try:
            await self._pipeline_publisher.after_publish(doc.id)
        except Exception as err:
            logger.warning("索引投递失败（不影响文档状态）：documentId=%s, %s", doc.id, err)

    async def safe_unpublish(self, document_id: str) -> None:
        try:
            await self._pipeline_publisher.after_unpublish(document_id)
        except Exception as err:
            logger.warning("索引清理投递失败：documentId=%s, %s", document_id, err)

    async def _find_by_id_or_throw(self, session: AsyncSession, doc_id: str) -> Document:
        stmt = select(Document).where(Document.id == doc_id, Document.deleted.is_(False))
        doc = (await session.execute(stmt)).scalars().first()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found")
        return doc

    def _assert_writable(self, doc: Document, actor: dict) -> None:
        if not can_write_document(doc, actor):
            raise HTTPException(403, "无权修改该文档")

    def build_content_summary(self, content: str, max_len: int = 200) -> str:
        trimmed = re.sub(r"\s+", " ", content.strip())
        return trimmed if len(trimmed) <= max_len else f"{trimmed[:max_len]}..."

    def count_words(self, content: str) -> int:
        trimmed = content.strip()
        if not trimmed:
            return 0
        cjk = len(re.findall(r"[\u4e00-\u9fff]", trimmed))
        latin = len(
            re.sub(r"[\u4e00-\u9fff]", " ", trimmed).strip().split()
        )
        return cjk + latin

    @staticmethod
    def _can_publish_from(status: int) -> bool:
        return status in (
            DocumentStatus.Draft,
            DocumentStatus.Published,
            DocumentStatus.Archived,
            DocumentStatus.PendingReview,
        )

    @staticmethod
    def _can_edit_content(status: int) -> bool:
        return status in (
            DocumentStatus.Draft,
            DocumentStatus.Published,
            DocumentStatus.Archived,
        )

    @staticmethod
    def _can_archive(status: int) -> bool:
        return status == DocumentStatus.Published


class _UploadMeta:
    """uploadAndCreateDocument 内部构造的 create 入参。"""

    def __init__(self, **kwargs) -> None:
        self.title = kwargs.get("title")
        self.content = kwargs.get("content")
        self.summary = kwargs.get("summary")
        self.categoryId = kwargs.get("categoryId")
        self.teamId = kwargs.get("teamId")
        self.coverImage = kwargs.get("coverImage")
        self.tags = kwargs.get("tags")
        self.status = kwargs.get("status")
        self.remark = kwargs.get("remark")
        self.isPublic = kwargs.get("isPublic")

    @classmethod
    def from_parse(cls, title: str, content: str, meta) -> "_UploadMeta":
        return cls(
            title=title,
            content=content,
            categoryId=meta.categoryId,
            teamId=meta.teamId,
            tags=meta.tags,
            status=DocumentStatus.Draft,
            remark=meta.remark,
            isPublic=meta.isPublic,
        )
