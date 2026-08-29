"""权限服务（树/分页/CRUD/分配/合并权限码）。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..common import (
    ADMIN_OPERATION_PERMISSIONS,
    ADMIN_ROLES,
    next_snowflake_id,
)
from ..models import (
    Permission,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)
from ..schemas import (
    AssignPermissionIdsDto,
    CreatePermissionDto,
    QueryPermissionDto,
    UpdatePermissionDto,
)


def _to_dict(perm: Permission) -> dict:
    return {
        "id": perm.id,
        "parentId": perm.parent_id,
        "permissionName": perm.permission_name,
        "permissionCode": perm.permission_code,
        "permissionType": perm.permission_type,
        "menuUrl": perm.menu_url,
        "apiUrl": perm.api_url,
        "method": perm.method,
        "icon": perm.icon,
        "sort": perm.sort,
        "status": perm.status,
        "createdAt": perm.created_at,
        "updatedAt": perm.updated_at,
    }


class PermissionService:
    async def list_all(self, session: AsyncSession) -> list[dict]:
        stmt = (
            select(Permission)
            .where(Permission.deleted.is_(False), Permission.status == 1)
            .order_by(Permission.sort.asc(), Permission.created_at.asc())
        )
        return [_to_dict(p) for p in (await session.execute(stmt)).scalars().all()]

    async def get_by_id(self, session: AsyncSession, perm_id: str) -> Permission:
        stmt = select(Permission).where(
            Permission.id == perm_id, Permission.deleted.is_(False)
        )
        perm = (await session.execute(stmt)).scalars().first()
        if not perm:
            raise HTTPException(404, "权限不存在")
        return perm

    async def get_children(self, session: AsyncSession, parent_id: str) -> list[dict]:
        stmt = (
            select(Permission)
            .where(
                Permission.parent_id == parent_id,
                Permission.deleted.is_(False),
                Permission.status == 1,
            )
            .order_by(Permission.sort.asc())
        )
        return [_to_dict(p) for p in (await session.execute(stmt)).scalars().all()]

    async def create(self, session: AsyncSession, dto: CreatePermissionDto) -> dict:
        stmt = select(Permission).where(
            Permission.permission_code == dto.permissionCode,
            Permission.deleted.is_(False),
        )
        if (await session.execute(stmt)).scalars().first():
            raise HTTPException(409, "权限编码已存在")

        perm = Permission(
            id=next_snowflake_id(),
            parent_id=dto.parentId or "0",
            permission_name=dto.permissionName,
            permission_code=dto.permissionCode,
            permission_type=dto.permissionType,
            menu_url=dto.menuUrl,
            api_url=dto.apiUrl,
            method=dto.method,
            icon=dto.icon,
            sort=dto.sort or 0,
            status=dto.status or 1,
        )
        session.add(perm)
        await session.flush()
        return _to_dict(perm)

    async def update(
        self, session: AsyncSession, perm_id: str, dto: UpdatePermissionDto
    ) -> dict:
        perm = await self.get_by_id(session, perm_id)
        if dto.permissionCode and dto.permissionCode != perm.permission_code:
            stmt = select(Permission).where(
                Permission.permission_code == dto.permissionCode,
                Permission.deleted.is_(False),
            )
            if (await session.execute(stmt)).scalars().first():
                raise HTTPException(409, "权限编码已存在")
            perm.permission_code = dto.permissionCode
        if dto.permissionName is not None:
            perm.permission_name = dto.permissionName
        if dto.permissionType is not None:
            perm.permission_type = dto.permissionType
        if dto.parentId is not None:
            if dto.parentId == perm_id:
                raise HTTPException(400, "父权限不能是自己")
            perm.parent_id = dto.parentId
        if dto.menuUrl is not None:
            perm.menu_url = dto.menuUrl
        if dto.apiUrl is not None:
            perm.api_url = dto.apiUrl
        if dto.method is not None:
            perm.method = dto.method
        if dto.icon is not None:
            perm.icon = dto.icon
        if dto.sort is not None:
            perm.sort = dto.sort
        if dto.status is not None:
            perm.status = dto.status
        await session.flush()
        return _to_dict(perm)

    async def delete(self, session: AsyncSession, perm_id: str) -> None:
        perm = await self.get_by_id(session, perm_id)
        child_stmt = select(func.count(Permission.id)).where(
            Permission.parent_id == perm_id, Permission.deleted.is_(False)
        )
        child_count = (await session.execute(child_stmt)).scalar() or 0
        if child_count > 0:
            raise HTTPException(400, "存在子权限，无法删除")

        role_bound_stmt = select(func.count(RolePermission.id)).where(
            RolePermission.permission_id == perm_id
        )
        user_bound_stmt = select(func.count(UserPermission.id)).where(
            UserPermission.permission_id == perm_id
        )
        role_bound = (await session.execute(role_bound_stmt)).scalar() or 0
        user_bound = (await session.execute(user_bound_stmt)).scalar() or 0
        if role_bound > 0 or user_bound > 0:
            raise HTTPException(400, "权限仍有关联角色或用户，无法删除")

        perm.deleted = True
        await session.flush()

    async def page(self, session: AsyncSession, query: QueryPermissionDto) -> dict:
        page = query.page or 1
        page_size = query.pageSize or 20
        stmt = select(Permission).where(Permission.deleted.is_(False))
        total_stmt = select(func.count(Permission.id)).where(
            Permission.deleted.is_(False)
        )
        if query.keyword and query.keyword.strip():
            kw = f"%{query.keyword.strip()}%"
            cond = or_(
                Permission.permission_name.ilike(kw),
                Permission.permission_code.ilike(kw),
            )
            stmt = stmt.where(cond)
            total_stmt = total_stmt.where(cond)

        stmt = (
            stmt.order_by(Permission.sort.asc(), Permission.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            _to_dict(p)
            for p in (await session.execute(stmt)).scalars().all()
        ]
        total = (await session.execute(total_stmt)).scalar() or 0
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    async def get_tree(self, session: AsyncSession) -> list[dict]:
        perms = await self.list_all(session)

        def build(parent_id: str) -> list[dict]:
            result = []
            for p in perms:
                if p["parentId"] == parent_id:
                    item = dict(p)
                    item["children"] = build(p["id"])
                    result.append(item)
            return result

        return build("0")

    async def get_role_permission_ids(self, session: AsyncSession, role_id: str) -> list[str]:
        await self._ensure_role_exists(session, role_id)
        stmt = select(RolePermission.permission_id).where(
            RolePermission.role_id == role_id
        )
        return [str(v) for v in (await session.execute(stmt)).scalars().all()]

    async def assign_role_permissions(
        self, session: AsyncSession, role_id: str, dto: AssignPermissionIdsDto
    ) -> list[str]:
        await self._ensure_role_exists(session, role_id)
        await self._validate_permission_ids(session, dto.permissionIds)
        await session.execute(
            sa_delete(RolePermission).where(RolePermission.role_id == role_id)
        )
        for perm_id in dto.permissionIds:
            session.add(
                RolePermission(
                    id=next_snowflake_id(), role_id=role_id, permission_id=perm_id
                )
            )
        await session.flush()
        return dto.permissionIds

    async def get_user_direct_permission_ids(self, session: AsyncSession, user_id: str) -> list[str]:
        stmt = select(UserPermission.permission_id).where(
            UserPermission.user_id == user_id
        )
        return [str(v) for v in (await session.execute(stmt)).scalars().all()]

    async def assign_user_permissions(
        self, session: AsyncSession, user_id: str, dto: AssignPermissionIdsDto
    ) -> list[str]:
        await self._validate_permission_ids(session, dto.permissionIds)
        await session.execute(
            sa_delete(UserPermission).where(UserPermission.user_id == user_id)
        )
        for perm_id in dto.permissionIds:
            session.add(
                UserPermission(
                    id=next_snowflake_id(), user_id=user_id, permission_id=perm_id
                )
            )
        await session.flush()
        return dto.permissionIds

    async def get_user_permission_codes(
        self, session: AsyncSession, user_id: str
    ) -> list[str]:
        # 直接权限
        direct_stmt = (
            select(Permission.permission_code)
            .join(UserPermission, UserPermission.permission_id == Permission.id)
            .where(
                UserPermission.user_id == user_id,
                Permission.status == 1,
                Permission.deleted.is_(False),
            )
        )
        direct = (await session.execute(direct_stmt)).scalars().all()

        # 角色权限
        via_role_stmt = (
            select(Permission.permission_code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == user_id,
                Role.status == 1,
                Permission.status == 1,
                Permission.deleted.is_(False),
            )
            .distinct()
        )
        via_role = (await session.execute(via_role_stmt)).scalars().all()

        codes: set[str] = set(direct) | set(via_role)

        role_stmt = (
            select(Role.role_code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, Role.status == 1)
        )
        role_codes = (await session.execute(role_stmt)).scalars().all()
        if any(rc in ADMIN_ROLES for rc in role_codes):
            codes.update(ADMIN_OPERATION_PERMISSIONS)

        return list(codes)

    async def _ensure_role_exists(self, session: AsyncSession, role_id: str) -> None:
        stmt = select(Role).where(Role.id == role_id)
        if not (await session.execute(stmt)).scalars().first():
            raise HTTPException(404, "角色不存在")

    async def _validate_permission_ids(
        self, session: AsyncSession, ids: list[str]
    ) -> None:
        if not ids:
            return
        stmt = select(func.count(Permission.id)).where(
            Permission.id.in_(ids),
            Permission.deleted.is_(False),
            Permission.status == 1,
        )
        found = (await session.execute(stmt)).scalar() or 0
        if found != len(ids):
            raise HTTPException(404, "部分权限不存在或已禁用")
