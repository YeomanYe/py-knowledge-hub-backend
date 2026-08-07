"""文档接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..schemas import CreateDocumentDto, QueryDocumentDto, UpdateDocumentDto
from ..services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


def _document_service() -> DocumentService:
    return DocumentService()


@router.post("")
async def create_document(
    dto: CreateDocumentDto, session: AsyncSession = Depends(get_session)
):
    return await _document_service().create(session, dto)


@router.get("")
async def find_all(
    query: QueryDocumentDto = Depends(), session: AsyncSession = Depends(get_session)
):
    return await _document_service().find_all(session, query)


@router.get("/{document_id}")
async def find_one(document_id: str, session: AsyncSession = Depends(get_session)):
    return await _document_service().find_one(session, document_id)


@router.patch("/{document_id}")
async def update_document(
    document_id: str,
    dto: UpdateDocumentDto,
    session: AsyncSession = Depends(get_session),
):
    return await _document_service().update(session, document_id, dto)


@router.delete("/{document_id}")
async def remove_document(
    document_id: str, session: AsyncSession = Depends(get_session)
):
    return await _document_service().remove(session, document_id)
