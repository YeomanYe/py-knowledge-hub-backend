"""SQLAlchemy 模型（1:1 对应 init.sql 表结构）。"""
from __future__ import annotations

import datetime
import typing

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    String,
    func,
)
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


class Base(DeclarativeBase):
    # 立即回读 server_default / onupdate 生成值，避免 flush 后属性过期触发惰性刷新
    __mapper_args__ = {"eager_defaults": True}


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
