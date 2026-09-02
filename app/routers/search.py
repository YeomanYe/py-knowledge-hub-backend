"""文档搜索接口（ES kh_document）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..container import Container
from ..deps import require_permission
from ..schemas import SearchDocumentsDto

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", dependencies=[Depends(require_permission("search"))])
async def search(dto: SearchDocumentsDto):
    return await Container.instance().search_index_service().search_documents(
        keyword=dto.keyword,
        page=dto.page,
        page_size=dto.pageSize,
        category_id=dto.categoryId,
        author_id=dto.authorId,
    )
