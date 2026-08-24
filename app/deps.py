"""FastAPI 鉴权依赖。"""
from __future__ import annotations

import logging

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .common import ADMIN_ROLES, RoleCode
from .config import get_settings
from .container import Container
from .database import get_session

logger = logging.getLogger("deps")

JWT_ALGORITHM = "HS256"


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """全局 JWT 鉴权：默认所有接口都需登录。"""
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录或 token 已失效")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(401, "未登录或 token 已失效")

    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as err:
        logger.debug("JWT 校验失败: %s", err)
        raise HTTPException(401, "未登录或 token 已失效")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "未登录或 token 已失效")

    container = Container.instance()
    try:
        auth_user = await container.user_service().build_auth_user(session, user_id)
    except Exception:
        raise HTTPException(401, "未登录或 token 已失效")
    return auth_user


def require_permission(*permissions: str):
    """权限码守卫。"""

    async def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if not permissions:
            return user
        if ADMIN_ROLES and any(r in user.get("roles", []) for r in ADMIN_ROLES):
            return user
        owned = set(user.get("permissions") or [])
        if not any(p in owned for p in permissions):
            raise HTTPException(403, "权限不足")
        return user

    return _dependency


def require_roles(*roles: str):
    """角色守卫。"""

    async def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if not roles:
            return user
        if not any(r in (user.get("roles") or []) for r in roles):
            raise HTTPException(403, "权限不足")
        return user

    return _dependency
