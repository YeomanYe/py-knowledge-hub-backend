"""认证服务（JWT 签发/登录/注册/刷新/邮箱激活/重置密码）。"""
from __future__ import annotations

import datetime
import random
import re

import jwt
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..redis_service import RedisService
from .email_activation import EmailActivationService
from .email_service import EmailService
from .password_reset import (
    PasswordResetService,
    RESET_CODE_COOLDOWN_SECONDS,
    RESET_CODE_TTL_SECONDS,
)
from .user_service import UserService

JWT_ALGORITHM = "HS256"

RESET_CODE_TTL = RESET_CODE_TTL_SECONDS
RESET_CODE_COOLDOWN = RESET_CODE_COOLDOWN_SECONDS


def _expires_in_seconds(expr: str, fallback: int) -> int:
    match = re.match(r"^(\d+)([smhd])$", expr)
    if not match:
        return fallback
    n = int(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return n
    if unit == "m":
        return n * 60
    if unit == "h":
        return n * 3600
    return n * 86400


class AuthService:
    def __init__(
        self,
        user_service: UserService,
        email_service: EmailService,
        email_activation: EmailActivationService,
        password_reset: PasswordResetService,
    ) -> None:
        self._user_service = user_service
        self._email_service = email_service
        self._email_activation = email_activation
        self._password_reset = password_reset
        self._settings = get_settings()

    # ------------------------------------------------------------ token
    def _sign_access_token(self, user: dict) -> str:
        payload = {
            "sub": user["userId"],
            "username": user["username"],
            "type": "access",
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=_expires_in_seconds(self._settings.jwt_access_expires, 7200)),
        }
        return jwt.encode(payload, self._settings.jwt_secret, algorithm=JWT_ALGORITHM)

    def _sign_refresh_token(self, user: dict) -> str:
        payload = {
            "sub": user["userId"],
            "username": user["username"],
            "type": "refresh",
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=_expires_in_seconds(self._settings.jwt_refresh_expires, 604800)),
        }
        return jwt.encode(payload, self._settings.jwt_secret, algorithm=JWT_ALGORITHM)

    def _access_expires_seconds(self) -> int:
        return _expires_in_seconds(self._settings.jwt_access_expires, 7200)

    def _require_email_verification(self) -> bool:
        return self._settings.require_email_verification

    def _build_login_result(self, user: dict) -> dict:
        return {
            "accessToken": self._sign_access_token(user),
            "refreshToken": self._sign_refresh_token(user),
            "tokenType": "Bearer",
            "expiresIn": self._access_expires_seconds(),
            "userInfo": user,
        }

    # ------------------------------------------------------------ 业务
    async def login(self, session: AsyncSession, username: str, password: str) -> dict:
        user = await self._user_service.validate_credentials(
            session, username, password
        )
        await self._user_service.touch_last_login(session, user["userId"])
        return self._build_login_result(user)

    async def register(
        self, session: AsyncSession, dto
    ) -> dict:
        result = await self._user_service.register(
            session,
            {
                "username": dto.username,
                "password": dto.password,
                "email": dto.email,
                "realName": dto.realName,
                "requireEmailVerification": self._require_email_verification(),
            },
        )

        if result["emailVerificationRequired"] and dto.email:
            token = await self._email_activation.create_token(result["userId"])
            try:
                await self._email_service.send_activation_email(
                    dto.email, dto.username, token
                )
            except Exception:
                await self._email_activation.delete_by_token(token)
                raise HTTPException(400, "激活邮件发送失败，请稍后再试")
            return {
                "userId": result["userId"],
                "message": "注册成功，请查收邮件激活账户",
                "emailVerificationRequired": True,
            }

        return {"userId": result["userId"], "message": "注册成功，请登录"}

    async def verify_email(self, session: AsyncSession, token: str) -> dict:
        user_id = await self._email_activation.consume_token(token)
        if not user_id:
            raise HTTPException(400, "激活链接无效或已过期")
        message = await self._user_service.activate_email(session, user_id)
        return {"message": message}

    async def send_reset_code(self, session: AsyncSession, email: str) -> dict:
        user = await self._user_service.find_by_email(session, email)
        if not user:
            raise HTTPException(404, "该邮箱未注册")

        ttl = await self._password_reset.get_ttl(email)
        if ttl > RESET_CODE_TTL_SECONDS - RESET_CODE_COOLDOWN_SECONDS:
            raise HTTPException(400, "验证码已发送，请稍后再试")

        code = str(random.randint(100000, 999999))
        await self._password_reset.set(email, code)
        try:
            await self._email_service.send_reset_code_email(
                email, user.username, code
            )
        except Exception:
            await self._password_reset.delete(email)
            raise HTTPException(400, "邮件发送失败，请稍后再试")
        return {"message": "验证码已发送"}

    async def reset_password_by_email(
        self, session: AsyncSession, email: str, code: str, new_password: str
    ) -> dict:
        if not await self._password_reset.verify(email, code):
            raise HTTPException(400, "验证码错误或已过期")
        await self._user_service.reset_password_by_email(
            session, email, new_password
        )
        await self._password_reset.delete(email)
        return {"message": "密码重置成功，请登录"}

    async def refresh(self, session: AsyncSession, refresh_token: str) -> dict:
        try:
            payload = jwt.decode(
                refresh_token, self._settings.jwt_secret, algorithms=[JWT_ALGORITHM]
            )
        except jwt.PyJWTError:
            raise HTTPException(401, "refresh token 无效或已过期")
        if payload.get("type") != "refresh":
            raise HTTPException(401, "无效的 refresh token")
        user = await self._user_service.build_auth_user(session, payload["sub"])
        return self._build_login_result(user)

    async def get_me(self, session: AsyncSession, user_id: str) -> dict:
        return await self._user_service.build_auth_user(session, user_id)

    async def build_auth_user(self, session: AsyncSession, user_id: str) -> dict:
        return await self._user_service.build_auth_user(session, user_id)

    async def get_reviewer_ids(self, session: AsyncSession) -> list[str]:
        return await self._user_service.get_user_ids_by_role_code(
            session, "ROLE_REVIEWER"
        )
