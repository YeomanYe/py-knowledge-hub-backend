"""团队管理接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..database import get_session
from ..deps import require_permission, require_roles
from ..schemas import CreateTeamDto, QueryTeamDto, UpdateTeamDto

router = APIRouter(
    prefix="/teams",
    tags=["teams"],
    dependencies=[Depends(require_roles("ROLE_ADMIN"))],
)

# 公开路由：团队树（无需登录）
public_router = APIRouter(prefix="/teams", tags=["teams"])


def _team_service():
    return Container.instance().team_service()


@public_router.get("/tree")
async def get_tree(
    root_only: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await _team_service().get_tree(session, root_only == "true")


@router.post("", dependencies=[Depends(require_permission("system:team"))])
async def create(dto: CreateTeamDto, session: AsyncSession = Depends(get_session)):
    return await _team_service().create(session, dto)


@router.put("/{team_id}", dependencies=[Depends(require_permission("system:team"))])
async def update(
    team_id: str, dto: UpdateTeamDto, session: AsyncSession = Depends(get_session)
):
    return await _team_service().update(session, team_id, dto)


@router.delete("/{team_id}", dependencies=[Depends(require_permission("system:team"))])
async def delete(team_id: str, session: AsyncSession = Depends(get_session)):
    await _team_service().delete(session, team_id)
    return {"message": "删除成功"}


@router.get("/page", dependencies=[Depends(require_permission("system:team"))])
async def page(
    query: QueryTeamDto = Depends(), session: AsyncSession = Depends(get_session)
):
    return await _team_service().page(session, query)


@router.get("/{team_id}", dependencies=[Depends(require_permission("system:team"))])
async def get_detail(team_id: str, session: AsyncSession = Depends(get_session)):
    return await _team_service().get_detail(session, team_id)


@router.post(
    "/{team_id}/members", dependencies=[Depends(require_permission("system:team"))]
)
async def add_members(
    team_id: str, body: list[str], session: AsyncSession = Depends(get_session)
):
    return await _team_service().add_members(session, team_id, body)


@router.delete(
    "/{team_id}/members", dependencies=[Depends(require_permission("system:team"))]
)
async def remove_members(
    team_id: str, body: list[str], session: AsyncSession = Depends(get_session)
):
    return await _team_service().remove_members(session, team_id, body)


@router.get(
    "/{team_id}/members", dependencies=[Depends(require_permission("system:team"))]
)
async def list_members(team_id: str, session: AsyncSession = Depends(get_session)):
    return await _team_service().list_members(session, team_id)
