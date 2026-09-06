"""SQLAlchemy 模型（1:1 对应 init.sql 表结构）。

雪花 ID 均以 BIGINT 存储、Python 侧用 str 表示
避免 JS Number 精度问题在 Python 侧同样以字符串贯穿业务逻辑）。
"""
from __future__ import annotations

import datetime
import typing

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

if typing.TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect


class SnowflakeIdType(TypeDecorator):
    """Postgres BIGINT ↔ Python str。"""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: typing.Any, dialect: Dialect) -> typing.Any:
        if value is None:
            return None
        return int(value)

    def process_result_value(self, value: typing.Any, dialect: Dialect) -> typing.Any:
        if value is None:
            return None
        return str(value)


def _now() -> datetime.datetime:
    return datetime.datetime.now()


class Base(DeclarativeBase):
    # 立即回读 server_default / onupdate 生成值，避免 flush 后属性过期触发惰性刷新
    __mapper_args__ = {"eager_defaults": True}


# ---------------------------------------------------------------- 用户 / 角色 / 权限
class User(Base):
    __tablename__ = "kh_user"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    username: Mapped[str] = mapped_column(String(50))
    password: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    real_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email_verified: Mapped[int] = mapped_column(SmallInteger, default=1)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class Role(Base):
    __tablename__ = "kh_role"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    role_name: Mapped[str] = mapped_column(String(50))
    role_code: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)


class UserRole(Base):
    __tablename__ = "kh_user_role"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    user_id: Mapped[str] = mapped_column(SnowflakeIdType, ForeignKey("kh_user.id"))
    role_id: Mapped[str] = mapped_column(SnowflakeIdType, ForeignKey("kh_role.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Permission(Base):
    __tablename__ = "kh_permission"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    parent_id: Mapped[str] = mapped_column(SnowflakeIdType, default="0")
    permission_name: Mapped[str] = mapped_column(String(50))
    permission_code: Mapped[str] = mapped_column(String(100), unique=True)
    permission_type: Mapped[int] = mapped_column(SmallInteger)
    menu_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class RolePermission(Base):
    __tablename__ = "kh_role_permission"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id"),
    )

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    role_id: Mapped[str] = mapped_column(SnowflakeIdType, ForeignKey("kh_role.id"))
    permission_id: Mapped[str] = mapped_column(
        SnowflakeIdType, ForeignKey("kh_permission.id")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class UserPermission(Base):
    __tablename__ = "kh_user_permission"
    __table_args__ = (
        UniqueConstraint("user_id", "permission_id"),
    )

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    user_id: Mapped[str] = mapped_column(SnowflakeIdType, ForeignKey("kh_user.id"))
    permission_id: Mapped[str] = mapped_column(
        SnowflakeIdType, ForeignKey("kh_permission.id")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ---------------------------------------------------------------- 团队
class Team(Base):
    __tablename__ = "kh_team"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    team_name: Mapped[str] = mapped_column(String(100))
    team_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    leader_id: Mapped[str | None] = mapped_column(SnowflakeIdType, nullable=True)
    parent_id: Mapped[str] = mapped_column(SnowflakeIdType, default="0")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class TeamMember(Base):
    __tablename__ = "kh_team_member"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    team_id: Mapped[str] = mapped_column(SnowflakeIdType, ForeignKey("kh_team.id"))
    user_id: Mapped[str] = mapped_column(SnowflakeIdType, ForeignKey("kh_user.id"))
    member_role: Mapped[str] = mapped_column(String(20), default="member")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ---------------------------------------------------------------- 文档
class DocumentStatus:
    Draft = 0
    Published = 1
    Archived = 2
    PendingReview = 3


class Document(Base):
    __tablename__ = "kh_document"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    content_id: Mapped[str] = mapped_column(String, unique=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    category_id: Mapped[str | None] = mapped_column(SnowflakeIdType, nullable=True)
    team_id: Mapped[str | None] = mapped_column(SnowflakeIdType, nullable=True)
    author_id: Mapped[str | None] = mapped_column(SnowflakeIdType, nullable=True)
    cover_image: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[int] = mapped_column(SmallInteger, default=DocumentStatus.Draft)
    remark: Mapped[str | None] = mapped_column(String, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    favourite_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    publish_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str | None] = mapped_column(SnowflakeIdType, nullable=True)
    update_by: Mapped[str | None] = mapped_column(SnowflakeIdType, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ReviewResult:
    Approved = 1
    Rejected = 2


class DocumentReview(Base):
    __tablename__ = "kh_document_review"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    document_id: Mapped[str] = mapped_column(SnowflakeIdType)
    reviewer_id: Mapped[str | None] = mapped_column(SnowflakeIdType, nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    review_result: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    review_comment: Mapped[str | None] = mapped_column(String, nullable=True)
    before_status: Mapped[int] = mapped_column(SmallInteger)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ---------------------------------------------------------------- AI 会话
class AiSession(Base):
    __tablename__ = "kh_ai_session"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    user_id: Mapped[str] = mapped_column(SnowflakeIdType)
    title: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AiMessage(Base):
    __tablename__ = "kh_ai_message"

    id: Mapped[str] = mapped_column(SnowflakeIdType, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        SnowflakeIdType, ForeignKey("kh_ai_session.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

