"""认证接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..database import get_session
from ..deps import get_current_user, require_roles
from ..schemas import (
    LoginDto,
    RefreshTokenDto,
    RegisterDto,
    ResetPasswordByEmailDto,
    SendResetCodeDto,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_service():
    return Container.instance().auth_service()


@router.post("/register")
async def register(dto: RegisterDto, session: AsyncSession = Depends(get_session)):
    return await _auth_service().register(session, dto)


@router.post("/login")
async def login(dto: LoginDto, session: AsyncSession = Depends(get_session)):
    return await _auth_service().login(session, dto.username, dto.password)


@router.post("/refresh")
async def refresh(dto: RefreshTokenDto, session: AsyncSession = Depends(get_session)):
    return await _auth_service().refresh(session, dto.refreshToken)


@router.get("/verify-email")
async def verify_email(
    token: str = Query(...), session: AsyncSession = Depends(get_session)
):
    return await _auth_service().verify_email(session, token)


@router.post("/password/reset/send-code")
async def send_reset_code(
    dto: SendResetCodeDto, session: AsyncSession = Depends(get_session)
):
    return await _auth_service().send_reset_code(session, dto.email)


@router.post("/password/reset")
async def reset_password(
    dto: ResetPasswordByEmailDto, session: AsyncSession = Depends(get_session)
):
    return await _auth_service().reset_password_by_email(session, dto)


@router.post("/logout")
async def logout(_user: dict = Depends(get_current_user)):
    return {"message": "已退出登录"}


@router.get("/me")
async def me(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    return await _auth_service().get_me(session, user["userId"])


@router.get(
    "/reviewer-ids",
    dependencies=[Depends(require_roles("ROLE_ADMIN", "ROLE_REVIEWER"))],
)
async def get_reviewer_ids(session: AsyncSession = Depends(get_session)):
    return await _auth_service().get_reviewer_ids(session)
