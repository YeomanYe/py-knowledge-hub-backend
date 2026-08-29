"""角色服务（list/get/create/update/delete）。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common import next_snowflake_id
from ..models import Role, UserRole
from ..schemas import CreateRoleDto, UpdateRoleDto


def _to_dict(role: Role) -> dict:
    return {
        "id": role.id,
        "roleName": role.role_name,
        "roleCode": role.role_code,
        "description": role.description,
        "status": role.status,
    }


class RoleService:
    async def list_all(self, session: AsyncSession) -> list[dict]:
        stmt = select(Role).where(Role.status == 1).order_by(Role.role_name.asc())
        return [_to_dict(r) for r in (await session.execute(stmt)).scalars().all()]

    async def get_by_id(self, session: AsyncSession, role_id: str) -> Role:
        stmt = select(Role).where(Role.id == role_id)
        role = (await session.execute(stmt)).scalars().first()
        if not role:
            raise HTTPException(404, "角色不存在")
        return role

    async def create(self, session: AsyncSession, dto: CreateRoleDto) -> dict:
        stmt = select(Role).where(Role.role_code == dto.roleCode)
        if (await session.execute(stmt)).scalars().first():
            raise HTTPException(409, "角色编码已存在")
        role = Role(
            id=next_snowflake_id(),
            role_name=dto.roleName,
            role_code=dto.roleCode,
            description=dto.description,
            status=1,
        )
        session.add(role)
        await session.flush()
        return _to_dict(role)

    async def update(self, session: AsyncSession, role_id: str, dto: UpdateRoleDto) -> dict:
        role = await self.get_by_id(session, role_id)
        if dto.roleName is not None:
            role.role_name = dto.roleName
        if dto.description is not None:
            role.description = dto.description
        if dto.status is not None:
            role.status = dto.status
        await session.flush()
        return _to_dict(role)

    async def delete(self, session: AsyncSession, role_id: str) -> None:
        role = await self.get_by_id(session, role_id)
        bound_stmt = select(func.count(UserRole.id)).where(
            UserRole.role_id == role_id
        )
        bound = (await session.execute(bound_stmt)).scalar() or 0
        if bound > 0:
            raise HTTPException(400, "角色仍有关联用户，无法删除")
        await session.delete(role)
        await session.flush()
