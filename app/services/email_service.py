"""邮件服务（激活邮件 + 密码重置验证码）。"""
from __future__ import annotations

import aiosmtplib
from email.message import EmailMessage

from app.config import get_settings


class EmailService:
    def __init__(self) -> None:
        self._config = get_settings()

    async def send_activation_email(self, email: str, username: str, token: str) -> None:
        base_url = self._config.app_public_url
        link = f"{base_url}/auth/verify-email?token={token}"
        await self._send(
            email,
            "激活您的知识库账户",
            f"您好 {username}，请点击以下链接激活账户（24 小时内有效）：\n{link}",
            f"""
            <p>您好 <strong>{username}</strong>，</p>
            <p>欢迎注册知识库，请点击下方链接激活账户（24 小时内有效）：</p>
            <p><a href="{link}">{link}</a></p>
            <p>如非本人操作，请忽略此邮件。</p>
            """,
        )

    async def send_reset_code_email(self, email: str, username: str, code: str) -> None:
        await self._send(
            email,
            "密码重置验证码",
            f"您好 {username}，您的密码重置验证码是：{code}，10 分钟内有效，请勿泄露。",
            f"""
            <p>您好 <strong>{username}</strong>，</p>
            <p>您正在重置密码，验证码为：</p>
            <p style="font-size:24px;font-weight:bold;letter-spacing:4px;">{code}</p>
            <p>验证码 10 分钟内有效，请勿泄露给他人。</p>
            <p>如非本人操作，请忽略此邮件。</p>
            """,
        )

    async def _send(self, to: str, subject: str, text: str, html: str) -> None:
        config = self._config
        message = EmailMessage()
        message["From"] = config.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        message.add_alternative(html, subtype="html")

        await aiosmtplib.send(
            message,
            hostname=config.mail_host,
            port=config.mail_port,
            username=config.mail_user,
            password=config.mail_pass,
            use_tls=config.mail_secure,
            start_tls=not config.mail_secure,
        )
