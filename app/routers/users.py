"""用户管理接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..database import get_session
from ..deps import get_current_user, require_permission, require_roles
from ..schemas import (
    AssignPermissionIdsDto,
    AssignRolesDto,
    ChangePasswordDto,
    CreateUserDto,
    QueryUserDto,
    ResetPasswordDto,
    UpdateProfileDto,
    UpdateUserDto,
)

router = APIRouter(prefix="/users", tags=["users"])

ADMIN_ONLY = Depends(require_roles("ROLE_ADMIN"))


def _user_service():
    return Container.instance().user_service()


def _permission_service():
    return Container.instance().permission_service()


@router.put("/me")
async def update_me(
    dto: UpdateProfileDto,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _user_service().update_profile(session, user["userId"], dto)


@router.get("/me/stats")
async def get_my_stats(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _user_service().get_user_statistics(session, user["userId"])


@router.put("/password/change")
async def change_password(
    dto: ChangePasswordDto,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    await _user_service().change_password(
        session, user["userId"], dto.oldPassword, dto.newPassword
    )
    return {"message": "密码修改成功"}


@router.get("/page", dependencies=[ADMIN_ONLY])
async def page_users(
    query: QueryUserDto = Depends(), session: AsyncSession = Depends(get_session)
):
    return await _user_service().page_users(session, query)


@router.get("/{user_id}", dependencies=[ADMIN_ONLY])
async def get_user(user_id: str, session: AsyncSession = Depends(get_session)):
    return await _user_service().get_user_vo(session, user_id)


@router.post("", dependencies=[ADMIN_ONLY])
async def create_user(dto: CreateUserDto, session: AsyncSession = Depends(get_session)):
    user_id = await _user_service().create_user(session, dto)
    return await _user_service().get_user_vo(session, user_id)


@router.put("/{user_id}", dependencies=[ADMIN_ONLY])
async def update_user(
    user_id: str, dto: UpdateUserDto, session: AsyncSession = Depends(get_session)
):
    return await _user_service().update_user(session, user_id, dto)


@router.delete("/{user_id}", dependencies=[ADMIN_ONLY])
async def delete_user(user_id: str, session: AsyncSession = Depends(get_session)):
    await _user_service().delete_user(session, user_id)
    return {"message": "删除成功"}


@router.put("/{user_id}/password/reset", dependencies=[ADMIN_ONLY])
async def reset_password(
    user_id: str, dto: ResetPasswordDto, session: AsyncSession = Depends(get_session)
):
    await _user_service().reset_password(session, user_id, dto.newPassword)
    return {"message": "密码重置成功"}


@router.get(
    "/{user_id}/roles",
    dependencies=[ADMIN_ONLY, Depends(require_permission("system:user"))],
)
async def get_user_roles(user_id: str, session: AsyncSession = Depends(get_session)):
    role_codes = await _user_service().get_user_role_codes(session, user_id)
    return {"userId": user_id, "roleCodes": role_codes}


@router.put(
    "/{user_id}/roles",
    dependencies=[ADMIN_ONLY, Depends(require_permission("system:user"))],
)
async def assign_roles(
    user_id: str, dto: AssignRolesDto, session: AsyncSession = Depends(get_session)
):
    role_codes = await _user_service().replace_roles(session, user_id, dto.roleCodes)
    return {"userId": user_id, "roleCodes": role_codes}


@router.get(
    "/{user_id}/permissions",
    dependencies=[ADMIN_ONLY, Depends(require_permission("system:user"))],
)
async def get_user_permissions(
    user_id: str, session: AsyncSession = Depends(get_session)
):
    await _user_service().find_by_id_or_throw(session, user_id)
    permission_codes = await _permission_service().get_user_permission_codes(
        session, user_id
    )
    permission_ids = await _permission_service().get_user_direct_permission_ids(
        session, user_id
    )
    return {
        "userId": user_id,
        "permissionCodes": permission_codes,
        "directPermissionIds": permission_ids,
    }


@router.put(
    "/{user_id}/permissions",
    dependencies=[ADMIN_ONLY, Depends(require_permission("system:user"))],
)
async def assign_user_permissions(
    user_id: str,
    dto: AssignPermissionIdsDto,
    session: AsyncSession = Depends(get_session),
):
    await _user_service().find_by_id_or_throw(session, user_id)
    permission_ids = await _permission_service().assign_user_permissions(
        session, user_id, dto
    )
    return {"userId": user_id, "permissionIds": permission_ids}
