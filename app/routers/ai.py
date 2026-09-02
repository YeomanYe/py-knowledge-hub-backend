"""AI / RAG 接口（混合检索）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..container import Container
from ..deps import require_permission
from ..schemas import RagSearchDto

router = APIRouter(tags=["ai"])

SEARCH_PERM = Depends(require_permission("search"))


@router.post("/rag/search", dependencies=[SEARCH_PERM])
async def rag_search(dto: RagSearchDto):
    hits = await Container.instance().hybrid_retrieval().retrieve(
        dto.query.strip(), dto.topK
    )
    return [h.__dict__ for h in hits]
