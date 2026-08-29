"""权限管理接口（全部 ROLE_ADMIN）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..database import get_session
from ..deps import require_permission, require_roles
from ..schemas import (
    CreatePermissionDto,
    QueryPermissionDto,
    UpdatePermissionDto,
)

router = APIRouter(
    prefix="/permissions",
    tags=["permissions"],
    dependencies=[Depends(require_roles("ROLE_ADMIN"))],
)


def _permission_service():
    return Container.instance().permission_service()


@router.get(
    "/list", dependencies=[Depends(require_permission("system:permission"))]
)
async def list_permissions(session: AsyncSession = Depends(get_session)):
    return await _permission_service().list_all(session)


@router.get(
    "/tree", dependencies=[Depends(require_permission("system:permission"))]
)
async def tree(session: AsyncSession = Depends(get_session)):
    return await _permission_service().get_tree(session)


@router.get(
    "/page", dependencies=[Depends(require_permission("system:permission"))]
)
async def page_permissions(
    query: QueryPermissionDto = Depends(), session: AsyncSession = Depends(get_session)
):
    return await _permission_service().page(session, query)


@router.get(
    "/{permission_id}/children",
    dependencies=[Depends(require_permission("system:permission"))],
)
async def children(permission_id: str, session: AsyncSession = Depends(get_session)):
    return await _permission_service().get_children(session, permission_id)


@router.get(
    "/{permission_id}",
    dependencies=[Depends(require_permission("system:permission"))],
)
async def get_permission(
    permission_id: str, session: AsyncSession = Depends(get_session)
):
    return await _permission_service().get_by_id(session, permission_id)


@router.post(
    "", dependencies=[Depends(require_permission("system:permission:create"))]
)
async def create_permission(
    dto: CreatePermissionDto, session: AsyncSession = Depends(get_session)
):
    return await _permission_service().create(session, dto)


@router.put(
    "/{permission_id}",
    dependencies=[Depends(require_permission("system:permission:edit"))],
)
async def update_permission(
    permission_id: str,
    dto: UpdatePermissionDto,
    session: AsyncSession = Depends(get_session),
):
    return await _permission_service().update(session, permission_id, dto)


@router.delete(
    "/{permission_id}",
    dependencies=[Depends(require_permission("system:permission:delete"))],
)
async def delete_permission(
    permission_id: str, session: AsyncSession = Depends(get_session)
):
    await _permission_service().delete(session, permission_id)
    return {"message": "删除成功"}
