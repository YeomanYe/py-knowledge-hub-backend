"""知识图谱接口（按当前用户可见范围过滤）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..container import Container
from ..deps import get_current_user, require_permission
from ..schemas import GraphOverviewDto, GraphQueryDto, GraphSearchDto

router = APIRouter(prefix="/graph", tags=["graph"])

GRAPH_PERM = Depends(require_permission("search"))


def _scope(user: dict):
    from ..document_access import access_from_user

    return access_from_user(user)


@router.get("/overview", dependencies=[GRAPH_PERM])
async def overview(
    query: GraphOverviewDto = Depends(),
    user: dict = Depends(get_current_user),
):
    graph = Container.instance().graph_build_service()
    return await graph.get_overview(
        keyword=query.keyword,
        entity_type=query.entityType,
        from_=query.from_,
        to=query.to,
        doc_limit=query.docLimit or 24,
        scope=_scope(user),
    )


@router.get("/search", dependencies=[GRAPH_PERM])
async def search(
    query: GraphSearchDto = Depends(),
    user: dict = Depends(get_current_user),
):
    return await Container.instance().graph_build_service().search_graph(
        query.keyword, query.limit or 50, _scope(user)
    )


@router.get("/nodes", dependencies=[GRAPH_PERM])
async def list_nodes(
    query: GraphQueryDto = Depends(),
    user: dict = Depends(get_current_user),
):
    return await Container.instance().graph_build_service().list_nodes(
        query.type, query.limit or 200, _scope(user)
    )


@router.get("/edges", dependencies=[GRAPH_PERM])
async def list_edges(
    query: GraphQueryDto = Depends(),
    user: dict = Depends(get_current_user),
):
    return await Container.instance().graph_build_service().list_edges(
        query.limit or 500, _scope(user)
    )
