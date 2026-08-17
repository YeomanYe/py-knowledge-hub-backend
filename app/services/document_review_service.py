"""文档发布审核服务（submit/approve/reject/任务列表）。"""
from __future__ import annotations

import datetime
import logging

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common import next_snowflake_id
from ..config import get_settings
from ..models import (
    Document,
    DocumentReview,
    DocumentStatus,
    ReviewResult,
)

logger = logging.getLogger("document-review")


def _review_to_dict(review: DocumentReview) -> dict:
    return {
        "id": review.id,
        "documentId": review.document_id,
        "reviewerId": review.reviewer_id,
        "reviewerName": review.reviewer_name,
        "reviewResult": review.review_result,
        "reviewComment": review.review_comment,
        "beforeStatus": review.before_status,
        "reviewedAt": review.reviewed_at,
        "createdAt": review.created_at,
    }


class DocumentReviewService:
    def __init__(self, pipeline_publisher) -> None:
        self._pipeline_publisher = pipeline_publisher
        self._settings = get_settings()

    def is_require_approval(self) -> bool:
        return self._settings.document_require_approval

    # ------------------------------------------------------------ 提交审核
    async def submit_for_review(
        self, session: AsyncSession, document_id: str, actor: dict | None = None
    ) -> Document:
        doc = await self._find_document_or_throw(session, document_id)

        if not self._can_submit_review(doc.status):
            raise HTTPException(400, "只有草稿或已发布状态的文档才能提交审核")

        pending_stmt = select(DocumentReview).where(
            DocumentReview.document_id == document_id,
            DocumentReview.review_result.is_(None),
        )
        pending = (await session.execute(pending_stmt)).scalars().first()
        if pending:
            raise HTTPException(400, "该文档已有待审核任务")

        before_status = doc.status
        review = DocumentReview(
            id=next_snowflake_id(),
            document_id=document_id,
            before_status=before_status,
        )
        session.add(review)
        await session.flush()

        doc.status = DocumentStatus.PendingReview
        if actor and actor.get("userId"):
            doc.update_by = actor["userId"]
        await session.flush()

        if before_status == DocumentStatus.Published:
            await self.safe_unpublish(document_id)

        logger.info(
            "文档已提交审核：documentId=%s, reviewId=%s, beforeStatus=%s",
            document_id, review.id, before_status,
        )
        return doc

    # ------------------------------------------------------------ 审核
    async def approve_review(
        self,
        session: AsyncSession,
        review_id: str,
        reviewer_id: str,
        reviewer_name: str,
        review_comment: str | None = None,
    ) -> Document:
        review = await self._find_pending_review_or_throw(session, review_id)

        review.review_result = ReviewResult.Approved
        review.reviewer_id = reviewer_id
        review.reviewer_name = reviewer_name
        review.review_comment = review_comment
        review.reviewed_at = datetime.datetime.now()
        await session.flush()

        doc = await self._find_document_or_throw(session, review.document_id)
        doc.status = DocumentStatus.Published
        doc.publish_time = datetime.datetime.now()
        await session.flush()
        await self.safe_publish(doc)

        logger.info("审核通过：reviewId=%s, documentId=%s", review_id, doc.id)
        return doc

    async def reject_review(
        self,
        session: AsyncSession,
        review_id: str,
        review_comment: str,
        reviewer_id: str,
        reviewer_name: str,
    ) -> Document:
        if not (review_comment or "").strip():
            raise HTTPException(400, "驳回意见不能为空")

        review = await self._find_pending_review_or_throw(session, review_id)

        review.review_result = ReviewResult.Rejected
        review.reviewer_id = reviewer_id
        review.reviewer_name = reviewer_name
        review.review_comment = review_comment.strip()
        review.reviewed_at = datetime.datetime.now()
        await session.flush()

        doc = await self._find_document_or_throw(session, review.document_id)
        doc.status = DocumentStatus.Draft
        await session.flush()

        logger.info("审核驳回：reviewId=%s, documentId=%s", review_id, doc.id)
        return doc

    # ------------------------------------------------------------ 查询
    async def list_tasks(self, session: AsyncSession, query) -> dict:
        page = query.page or 1
        page_size = query.pageSize or 20
        stmt = select(DocumentReview)
        total_stmt = select(func.count(DocumentReview.id))

        if query.status == "pending" or not query.status:
            stmt = stmt.where(DocumentReview.review_result.is_(None))
            total_stmt = total_stmt.where(DocumentReview.review_result.is_(None))
        elif query.status == "approved":
            stmt = stmt.where(DocumentReview.review_result == ReviewResult.Approved)
            total_stmt = total_stmt.where(
                DocumentReview.review_result == ReviewResult.Approved
            )
        elif query.status == "rejected":
            stmt = stmt.where(DocumentReview.review_result == ReviewResult.Rejected)
            total_stmt = total_stmt.where(
                DocumentReview.review_result == ReviewResult.Rejected
            )

        stmt = (
            stmt.order_by(DocumentReview.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            _review_to_dict(r) for r in (await session.execute(stmt)).scalars().all()
        ]
        total = (await session.execute(total_stmt)).scalar() or 0
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    async def get_pending_count(self, session: AsyncSession) -> int:
        stmt = select(func.count(DocumentReview.id)).where(
            DocumentReview.review_result.is_(None)
        )
        return (await session.execute(stmt)).scalar() or 0

    async def get_current_review(
        self, session: AsyncSession, document_id: str
    ) -> dict | None:
        stmt = (
            select(DocumentReview)
            .where(
                DocumentReview.document_id == document_id,
                DocumentReview.review_result.is_(None),
            )
            .order_by(DocumentReview.created_at.desc())
        )
        review = (await session.execute(stmt)).scalars().first()
        return _review_to_dict(review) if review else None

    async def get_review_history(
        self, session: AsyncSession, document_id: str
    ) -> list[dict]:
        stmt = (
            select(DocumentReview)
            .where(DocumentReview.document_id == document_id)
            .order_by(DocumentReview.created_at.desc())
        )
        return [
            _review_to_dict(r) for r in (await session.execute(stmt)).scalars().all()
        ]

    # ------------------------------------------------------------ 内部
    async def safe_publish(self, doc: Document) -> None:
        try:
            await self._pipeline_publisher.after_publish(doc.id)
        except Exception as err:  # noqa: BLE001
            logger.warning("审核通过后索引投递失败：documentId=%s, %s", doc.id, err)

    async def safe_unpublish(self, document_id: str) -> None:
        try:
            await self._pipeline_publisher.after_unpublish(document_id)
        except Exception as err:  # noqa: BLE001
            logger.warning("提交审核后索引清理失败：documentId=%s, %s", document_id, err)

    async def _find_pending_review_or_throw(
        self, session: AsyncSession, review_id: str
    ) -> DocumentReview:
        stmt = select(DocumentReview).where(DocumentReview.id == review_id)
        review = (await session.execute(stmt)).scalars().first()
        if not review:
            raise HTTPException(404, f"Review {review_id} not found")
        if review.review_result is not None:
            raise HTTPException(400, "该审核任务已处理")
        return review

    async def _find_document_or_throw(
        self, session: AsyncSession, doc_id: str
    ) -> Document:
        stmt = select(Document).where(Document.id == doc_id, Document.deleted.is_(False))
        doc = (await session.execute(stmt)).scalars().first()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found")
        return doc

    @staticmethod
    def _can_submit_review(status: int) -> bool:
        return status in (DocumentStatus.Draft, DocumentStatus.Published)
