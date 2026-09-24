"""文档搜索接口（ES kh_document，按当前用户可见范围过滤）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..container import Container
from ..deps import get_current_user, require_permission
from ..schemas import SearchDocumentsDto

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", dependencies=[Depends(require_permission("search"))])
async def search(
    dto: SearchDocumentsDto,
    user: dict = Depends(get_current_user),
):
    from ..document_access import access_from_user

    return await Container.instance().search_index_service().search_documents(
        keyword=dto.keyword,
        page=dto.page,
        page_size=dto.pageSize,
        category_id=dto.categoryId,
        author_id=dto.authorId,
        scope=access_from_user(user),
    )
