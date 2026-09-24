"""团队管理接口。

- GET /teams/mine：登录即可，返回当前用户所在团队（含担任负责人的）
- 其余接口全部 ADMIN
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..database import get_session
from ..deps import get_current_user, require_permission, require_roles
from ..schemas import CreateTeamDto, QueryTeamDto, UpdateTeamDto

router = APIRouter(
    prefix="/teams",
    tags=["teams"],
    dependencies=[Depends(require_roles("ROLE_ADMIN"))],
)

# 登录即可访问的轻量路由
mine_router = APIRouter(prefix="/teams", tags=["teams"])


def _team_service():
    return Container.instance().team_service()


@mine_router.get("/mine")
async def list_mine(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _team_service().list_mine(session, user["userId"])


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


@router.get("/tree", dependencies=[Depends(require_permission("system:team"))])
async def get_tree(
    root_only: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await _team_service().get_tree(session, root_only == "true")


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
