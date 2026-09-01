"""知识图谱接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..container import Container
from ..deps import require_permission
from ..schemas import GraphOverviewDto, GraphQueryDto, GraphSearchDto

router = APIRouter(prefix="/graph", tags=["graph"])

GRAPH_PERM = Depends(require_permission("search"))


@router.get("/overview", dependencies=[GRAPH_PERM])
async def overview(query: GraphOverviewDto = Depends()):
    graph = Container.instance().graph_build_service()
    return await graph.get_overview(
        keyword=query.keyword,
        entity_type=query.entityType,
        from_=query.from_,
        to=query.to,
        doc_limit=query.docLimit or 24,
    )


@router.get("/search", dependencies=[GRAPH_PERM])
async def search(query: GraphSearchDto = Depends()):
    return await Container.instance().graph_build_service().search_graph(
        query.keyword, query.limit or 50
    )


@router.get("/nodes", dependencies=[GRAPH_PERM])
async def list_nodes(query: GraphQueryDto = Depends()):
    return await Container.instance().graph_build_service().list_nodes(
        query.type, query.limit or 200
    )


@router.get("/edges", dependencies=[GRAPH_PERM])
async def list_edges(query: GraphQueryDto = Depends()):
    return await Container.instance().graph_build_service().list_edges(
        query.limit or 500
    )
