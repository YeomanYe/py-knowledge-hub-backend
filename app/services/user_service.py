"""用户服务（注册/登录校验/CRUD/角色分配/统计）。"""
from __future__ import annotations

import datetime

import bcrypt
from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..common import next_snowflake_id, RoleCode
from ..models import Document, Role, User, UserRole
from ..schemas import (
    CreateUserDto,
    QueryUserDto,
    UpdateProfileDto,
    UpdateUserDto,
)
from .permission_service import PermissionService


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(10)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _unauthorized(message: str = "未登录或 token 已失效") -> HTTPException:
    return HTTPException(401, message)


class UserService:
    def __init__(self, permission_service: PermissionService) -> None:
        self._permission_service = permission_service

    # ------------------------------------------------------------ 基础查询
    async def find_by_email(self, session: AsyncSession, email: str) -> User | None:
        stmt = select(User).where(User.email == email, User.deleted.is_(False))
        return (await session.execute(stmt)).scalars().first()

    async def find_by_username(self, session: AsyncSession, username: str) -> User | None:
        stmt = select(User).where(User.username == username, User.deleted.is_(False))
        return (await session.execute(stmt)).scalars().first()

    async def find_by_id_or_throw(self, session: AsyncSession, user_id: str) -> User:
        stmt = select(User).where(User.id == user_id, User.deleted.is_(False))
        user = (await session.execute(stmt)).scalars().first()
        if not user:
            raise HTTPException(404, "用户不存在")
        return user

    async def get_role_codes(self, session: AsyncSession, user_id: str) -> list[str]:
        stmt = (
            select(Role.role_code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, Role.status == 1)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    # ------------------------------------------------------------ VO / AuthUser
    async def to_user_vo(self, session: AsyncSession, user: User) -> dict:
        role_codes = await self.get_role_codes(session, user.id)
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "realName": user.real_name,
            "avatar": user.avatar,
            "status": user.status,
            "lastLoginAt": user.last_login_at,
            "createdAt": user.created_at,
            "updatedAt": user.updated_at,
            "roleCodes": role_codes,
        }

    def to_auth_user(
        self, user: User, roles: list[str], permissions: list[str]
    ) -> dict:
        return {
            "userId": user.id,
            "username": user.username,
            "realName": user.real_name,
            "email": user.email,
            "avatar": user.avatar,
            "roles": roles,
            "permissions": permissions,
        }

    async def build_auth_user(self, session: AsyncSession, user_id: str) -> dict:
        user = await self.find_by_id_or_throw(session, user_id)
        if user.status != 1:
            raise _unauthorized("账户已禁用")
        roles = await self.get_role_codes(session, user_id)
        permissions = await self._permission_service.get_user_permission_codes(
            session, user_id
        )
        return self.to_auth_user(user, roles, permissions)

    async def validate_credentials(
        self, session: AsyncSession, username: str, password: str
    ) -> dict:
        user = await self.find_by_username(session, username)
        if not user:
            raise _unauthorized("用户名或密码错误")
        if user.status != 1:
            raise _unauthorized("账户已禁用")
        if user.email_verified == 0:
            raise _unauthorized("账户未激活，请先验证邮箱")
        if not verify_password(password, user.password):
            raise _unauthorized("用户名或密码错误")
        roles = await self.get_role_codes(session, user.id)
        permissions = await self._permission_service.get_user_permission_codes(
            session, user.id
        )
        return self.to_auth_user(user, roles, permissions)

    # ------------------------------------------------------------ 注册 / 创建
    async def register(
        self,
        session: AsyncSession,
        input_: dict,
    ) -> dict:
        exists = await self.find_by_username(session, input_["username"])
        if exists:
            raise HTTPException(409, "用户名已存在")
        if input_.get("email"):
            email_used = await self.find_by_email(session, input_["email"])
            if email_used:
                raise HTTPException(409, "邮箱已被使用")

        user_id = next_snowflake_id()
        need_verify = bool(input_.get("requireEmailVerification")) and bool(
            input_.get("email")
        )
        user = User(
            id=user_id,
            username=input_["username"],
            password=hash_password(input_["password"]),
            email=input_.get("email"),
            real_name=input_.get("realName"),
            status=0 if need_verify else 1,
            email_verified=0 if need_verify else 1,
        )
        session.add(user)
        await session.flush()
        await self.assign_role(session, user_id, RoleCode.USER)

        return {"userId": user_id, "emailVerificationRequired": need_verify}

    async def create_user(self, session: AsyncSession, dto: CreateUserDto) -> str:
        exists = await self.find_by_username(session, dto.username)
        if exists:
            raise HTTPException(409, "用户名已存在")

        user_id = next_snowflake_id()
        user = User(
            id=user_id,
            username=dto.username,
            password=hash_password(dto.password),
            email=dto.email,
            real_name=dto.realName,
            avatar=dto.avatar,
            status=dto.status if dto.status is not None else 1,
            email_verified=1,
        )
        session.add(user)
        await session.flush()

        role_codes = dto.roleCodes if dto.roleCodes else [RoleCode.USER]
        await self.replace_roles(session, user_id, role_codes)
        return user_id

    # ------------------------------------------------------------ 更新 / 删除
    async def update_user(self, session: AsyncSession, user_id: str, dto: UpdateUserDto) -> dict:
        user = await self.find_by_id_or_throw(session, user_id)
        if dto.email is not None:
            user.email = dto.email
        if dto.realName is not None:
            user.real_name = dto.realName
        if dto.avatar is not None:
            user.avatar = dto.avatar
        if dto.status is not None:
            user.status = dto.status
        await session.flush()
        return await self.to_user_vo(session, user)

    async def delete_user(self, session: AsyncSession, user_id: str) -> None:
        user = await self.find_by_id_or_throw(session, user_id)
        user.deleted = True
        await session.flush()

    # ------------------------------------------------------------ 分页
    async def page_users(self, session: AsyncSession, query: QueryUserDto) -> dict:
        page = query.page or 1
        page_size = query.pageSize or 20
        stmt = select(User).where(User.deleted.is_(False))

        if query.keyword and query.keyword.strip():
            kw = f"%{query.keyword.strip()}%"
            stmt = stmt.where(
                or_(
                    User.username.ilike(kw),
                    User.real_name.ilike(kw),
                    User.email.ilike(kw),
                )
            )
        if query.status is not None:
            stmt = stmt.where(User.status == query.status)
        if query.roleCode:
            stmt = (
                stmt.join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.role_code == query.roleCode)
            )

        stmt = (
            stmt.order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        users = result.scalars().unique().all()

        total_stmt = select(func.count(func.distinct(User.id))).where(
            User.deleted.is_(False)
        )
        if query.keyword and query.keyword.strip():
            kw = f"%{query.keyword.strip()}%"
            total_stmt = total_stmt.where(
                or_(
                    User.username.ilike(kw),
                    User.real_name.ilike(kw),
                    User.email.ilike(kw),
                )
            )
        if query.status is not None:
            total_stmt = total_stmt.where(User.status == query.status)
        if query.roleCode:
            total_stmt = (
                total_stmt.join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.role_code == query.roleCode)
            )
        total = (await session.execute(total_stmt)).scalar() or 0

        items = [await self.to_user_vo(session, u) for u in users]
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    async def get_user_vo(self, session: AsyncSession, user_id: str) -> dict:
        user = await self.find_by_id_or_throw(session, user_id)
        return await self.to_user_vo(session, user)

    async def get_user_role_codes(self, session: AsyncSession, user_id: str) -> list[str]:
        await self.find_by_id_or_throw(session, user_id)
        return await self.get_role_codes(session, user_id)

    # ------------------------------------------------------------ 角色分配
    async def replace_roles(
        self, session: AsyncSession, user_id: str, role_codes: list[str]
    ) -> list[str]:
        await self.find_by_id_or_throw(session, user_id)

        stmt = select(Role).where(
            Role.role_code.in_(role_codes), Role.status == 1
        )
        roles = (await session.execute(stmt)).scalars().all()
        if len(roles) != len(role_codes):
            found = {r.role_code for r in roles}
            missing = [c for c in role_codes if c not in found]
            raise HTTPException(404, f"角色不存在: {', '.join(missing)}")

        from sqlalchemy import delete as sa_delete

        await session.execute(sa_delete(UserRole).where(UserRole.user_id == user_id))
        for role in roles:
            session.add(UserRole(id=next_snowflake_id(), user_id=user_id, role_id=role.id))
        await session.flush()
        return role_codes

    async def assign_role(self, session: AsyncSession, user_id: str, role_code: str) -> None:
        stmt = select(Role).where(Role.role_code == role_code)
        role = (await session.execute(stmt)).scalars().first()
        if not role:
            raise HTTPException(404, f"角色 {role_code} 不存在")
        exists_stmt = select(UserRole).where(
            UserRole.user_id == user_id, UserRole.role_id == role.id
        )
        exists = (await session.execute(exists_stmt)).scalars().first()
        if exists:
            return
        session.add(UserRole(id=next_snowflake_id(), user_id=user_id, role_id=role.id))
        await session.flush()

    async def touch_last_login(self, session: AsyncSession, user_id: str) -> None:
        await session.execute(
            update(User).where(User.id == user_id).values(last_login_at=datetime.datetime.now())
        )

    async def get_user_ids_by_role_code(self, session: AsyncSession, role_code: str) -> list[str]:
        stmt = (
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                Role.role_code == role_code,
                User.deleted.is_(False),
                User.status == 1,
            )
        )
        return [str(v) for v in (await session.execute(stmt)).scalars().all()]

    async def list_all_roles(self, session: AsyncSession) -> list[Role]:
        stmt = select(Role).where(Role.status == 1).order_by(Role.role_name.asc())
        return list((await session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------ 个人资料 / 密码
    async def update_profile(self, session: AsyncSession, user_id: str, dto: UpdateProfileDto) -> dict:
        user = await self.find_by_id_or_throw(session, user_id)
        if dto.email is not None:
            user.email = dto.email
        if dto.realName is not None:
            user.real_name = dto.realName
        if dto.avatar is not None:
            user.avatar = dto.avatar
        await session.flush()
        return await self.to_user_vo(session, user)

    async def change_password(
        self, session: AsyncSession, user_id: str, old_password: str, new_password: str
    ) -> None:
        user = await self.find_by_id_or_throw(session, user_id)
        if not verify_password(old_password, user.password):
            raise HTTPException(400, "原密码错误")
        user.password = hash_password(new_password)
        await session.flush()

    async def reset_password(self, session: AsyncSession, user_id: str, new_password: str) -> None:
        user = await self.find_by_id_or_throw(session, user_id)
        user.password = hash_password(new_password)
        await session.flush()

    async def reset_password_by_email(
        self, session: AsyncSession, email: str, new_password: str
    ) -> None:
        user = await self.find_by_email(session, email)
        if not user:
            raise HTTPException(404, "该邮箱未注册")
        user.password = hash_password(new_password)
        await session.flush()

    async def activate_email(self, session: AsyncSession, user_id: str) -> str:
        user = await self.find_by_id_or_throw(session, user_id)
        if user.email_verified == 1:
            return "账户已激活，请直接登录"
        user.email_verified = 1
        user.status = 1
        await session.flush()
        return "账户激活成功，请登录"

    # ------------------------------------------------------------ 统计
    async def get_user_statistics(self, session: AsyncSession, user_id: str) -> dict:
        await self.find_by_id_or_throw(session, user_id)
        document_count_stmt = (
            select(func.count(Document.id))
            .where(Document.author_id == user_id, Document.deleted.is_(False))
        )
        document_count = (await session.execute(document_count_stmt)).scalar() or 0

        sums_stmt = (
            select(
                func.coalesce(func.sum(Document.view_count), 0),
                func.coalesce(func.sum(Document.like_count), 0),
                func.coalesce(func.sum(Document.comment_count), 0),
            )
            .where(Document.author_id == user_id, Document.deleted.is_(False))
        )
        view_count, like_count, comment_count = (
            await session.execute(sums_stmt)
        ).one()
        return {
            "documentCount": document_count,
            "viewCount": int(view_count or 0),
            "likeCount": int(like_count or 0),
            "commentCount": int(comment_count or 0),
        }
