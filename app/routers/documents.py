"""文档接口（JWT + 权限码；审核另需 ROLE_REVIEWER/ADMIN）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..database import get_session
from ..deps import get_current_user, require_permission, require_roles
from ..schemas import (
    CreateDocumentDto,
    QueryDocumentDto,
    QueryReviewTasksDto,
    ReviewDecisionDto,
    UpdateDocumentDto,
    UploadParseDto,
)

router = APIRouter(prefix="/documents", tags=["documents"])

REVIEWER_OR_ADMIN = Depends(require_roles("ROLE_REVIEWER", "ROLE_ADMIN"))
DOC_LIST = Depends(require_permission("document:list"))
DOC_REVIEW = Depends(require_permission("document:review"))


def _document_service():
    return Container.instance().document_service()


def _review_service():
    return Container.instance().review_service()


@router.post("", dependencies=[Depends(require_permission("document:create"))])
async def create_document(
    dto: CreateDocumentDto,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().create(session, dto, user)


@router.post(
    "/upload/parse",
    dependencies=[Depends(require_permission("document:create"))],
)
async def upload_and_parse(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
    file: UploadFile | None = File(default=None),
    categoryId: str | None = Form(default=None),
    teamId: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    remark: str | None = Form(default=None),
    isPublic: str | None = Form(default=None),
):
    if file is None:
        raise HTTPException(400, "请上传文件（form-data 字段名: file）")
    meta = UploadParseDto.model_validate(
        {
            "categoryId": categoryId,
            "teamId": teamId,
            "tags": tags,
            "remark": remark,
            "isPublic": isPublic,
        }
    )
    return await _document_service().upload_and_create_document(
        session, file, meta, user
    )


# 静态路由需在 /:id 之前注册
@router.get(
    "/reviews/tasks",
    dependencies=[REVIEWER_OR_ADMIN, DOC_REVIEW],
)
async def list_review_tasks(
    query: QueryReviewTasksDto = Depends(), session: AsyncSession = Depends(get_session)
):
    return await _review_service().list_tasks(session, query)


@router.get(
    "/reviews/tasks/pending-count",
    dependencies=[REVIEWER_OR_ADMIN, DOC_REVIEW],
)
async def pending_review_count(session: AsyncSession = Depends(get_session)):
    return {"count": await _review_service().get_pending_count(session)}


@router.get("", dependencies=[DOC_LIST])
async def find_all(
    query: QueryDocumentDto = Depends(),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().find_all(session, query, user)


@router.put(
    "/{document_id}/publish",
    dependencies=[Depends(require_permission("document:edit"))],
)
async def publish(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().publish(session, document_id, user)


@router.put(
    "/{document_id}/archive",
    dependencies=[Depends(require_permission("document:edit"))],
)
async def archive(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().archive(session, document_id, user)


@router.put(
    "/{document_id}/save-draft",
    dependencies=[Depends(require_permission("document:edit"))],
)
async def save_as_draft(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().save_as_draft(session, document_id, user)


@router.post(
    "/{document_id}/reviews/submit",
    dependencies=[Depends(require_permission("document:edit"))],
)
async def submit_review(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _review_service().submit_for_review(session, document_id, user)


@router.get(
    "/{document_id}/reviews/current",
    dependencies=[Depends(require_permission("document:list", "document:review"))],
)
async def get_current_review(
    document_id: str, session: AsyncSession = Depends(get_session)
):
    return await _review_service().get_current_review(session, document_id)


@router.get(
    "/{document_id}/reviews/history",
    dependencies=[Depends(require_permission("document:list", "document:review"))],
)
async def get_review_history(
    document_id: str, session: AsyncSession = Depends(get_session)
):
    return await _review_service().get_review_history(session, document_id)


@router.post(
    "/reviews/tasks/{task_id}/approve",
    dependencies=[REVIEWER_OR_ADMIN, DOC_REVIEW],
)
async def approve_review(
    task_id: str,
    dto: ReviewDecisionDto,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _review_service().approve_review(
        session,
        task_id,
        user["userId"],
        user.get("realName") or user["username"],
        dto.reviewComment,
    )


@router.post(
    "/reviews/tasks/{task_id}/reject",
    dependencies=[REVIEWER_OR_ADMIN, DOC_REVIEW],
)
async def reject_review(
    task_id: str,
    dto: ReviewDecisionDto,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _review_service().reject_review(
        session,
        task_id,
        dto.reviewComment or "",
        user["userId"],
        user.get("realName") or user["username"],
    )


@router.get("/{document_id}", dependencies=[DOC_LIST])
async def find_one(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().find_one(session, document_id, True, user)


@router.patch(
    "/{document_id}",
    dependencies=[Depends(require_permission("document:edit"))],
)
async def update_document(
    document_id: str,
    dto: UpdateDocumentDto,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().update(session, document_id, dto, user)


@router.delete(
    "/{document_id}",
    dependencies=[Depends(require_permission("document:delete"))],
)
async def remove_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _document_service().remove(session, document_id, user)
