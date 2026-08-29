"""角色管理接口（全部 ROLE_ADMIN）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..database import get_session
from ..deps import require_permission, require_roles
from ..schemas import AssignPermissionIdsDto, CreateRoleDto, UpdateRoleDto

router = APIRouter(
    prefix="/roles",
    tags=["roles"],
    dependencies=[Depends(require_roles("ROLE_ADMIN"))],
)


def _role_service():
    return Container.instance().role_service()


def _permission_service():
    return Container.instance().permission_service()


@router.get("/list")
async def list_roles(session: AsyncSession = Depends(get_session)):
    return await _role_service().list_all(session)


@router.get("/{role_id}")
async def get_role(role_id: str, session: AsyncSession = Depends(get_session)):
    role = await _role_service().get_by_id(session, role_id)
    return {
        "id": role.id,
        "roleName": role.role_name,
        "roleCode": role.role_code,
        "description": role.description,
        "status": role.status,
    }


@router.post("")
async def create_role(dto: CreateRoleDto, session: AsyncSession = Depends(get_session)):
    return await _role_service().create(session, dto)


@router.put("/{role_id}")
async def update_role(
    role_id: str, dto: UpdateRoleDto, session: AsyncSession = Depends(get_session)
):
    return await _role_service().update(session, role_id, dto)


@router.delete("/{role_id}")
async def delete_role(role_id: str, session: AsyncSession = Depends(get_session)):
    await _role_service().delete(session, role_id)
    return {"message": "删除成功"}


@router.get(
    "/{role_id}/permissions",
    dependencies=[Depends(require_permission("system:role"))],
)
async def get_role_permissions(
    role_id: str, session: AsyncSession = Depends(get_session)
):
    permission_ids = await _permission_service().get_role_permission_ids(
        session, role_id
    )
    return {"roleId": role_id, "permissionIds": permission_ids}


@router.put(
    "/{role_id}/permissions",
    dependencies=[Depends(require_permission("system:role"))],
)
async def assign_role_permissions(
    role_id: str,
    dto: AssignPermissionIdsDto,
    session: AsyncSession = Depends(get_session),
):
    permission_ids = await _permission_service().assign_role_permissions(
        session, role_id, dto
    )
    return {"roleId": role_id, "permissionIds": permission_ids}
