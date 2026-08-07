"""Pydantic 请求/响应模型（对应 class-validator DTO）。

校验语义对齐 NestJS ValidationPipe：
- whitelist + forbidNonWhitelisted → extra='forbid'（未知字段直接 422）
- transform → Pydantic 自动做类型转换（query/form 的字符串数字等）
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import DocumentStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- auth
class LoginDto(ApiModel):
    username: str
    password: str


class RegisterDto(ApiModel):
    username: str
    password: str = Field(min_length=6)
    email: EmailStr | None = None
    realName: str | None = None


class RefreshTokenDto(ApiModel):
    refreshToken: str


# ---------------------------------------------------------------- user
class QueryUserDto(ApiModel):
    keyword: str | None = None
    roleCode: str | None = None
    status: int | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class CreateUserDto(ApiModel):
    username: str
    password: str = Field(min_length=6)
    email: EmailStr | None = None
    realName: str | None = None
    avatar: str | None = None
    status: int | None = None
    roleCodes: list[str] | None = None


class UpdateUserDto(ApiModel):
    email: EmailStr | None = None
    realName: str | None = None
    avatar: str | None = None
    status: int | None = None


class AssignRolesDto(ApiModel):
    roleCodes: list[str] = Field(min_length=1)


class UpdateProfileDto(ApiModel):
    email: EmailStr | None = None
    realName: str | None = None
    avatar: str | None = None


class ChangePasswordDto(ApiModel):
    oldPassword: str
    newPassword: str = Field(min_length=6)


class ResetPasswordDto(ApiModel):
    newPassword: str = Field(min_length=6)


class SendResetCodeDto(ApiModel):
    email: EmailStr


class ResetPasswordByEmailDto(ApiModel):
    email: EmailStr
    code: str
    newPassword: str = Field(min_length=6)


# ---------------------------------------------------------------- role / permission
class CreateRoleDto(ApiModel):
    roleName: str
    roleCode: str
    description: str | None = None


class UpdateRoleDto(ApiModel):
    roleName: str | None = None
    description: str | None = None
    status: int | None = None


class CreatePermissionDto(ApiModel):
    permissionName: str
    permissionCode: str
    permissionType: int
    parentId: str | None = None
    menuUrl: str | None = None
    apiUrl: str | None = None
    method: str | None = None
    icon: str | None = None
    sort: int | None = None
    status: int | None = None


class UpdatePermissionDto(ApiModel):
    permissionName: str | None = None
    permissionCode: str | None = None
    permissionType: int | None = None
    parentId: str | None = None
    menuUrl: str | None = None
    apiUrl: str | None = None
    method: str | None = None
    icon: str | None = None
    sort: int | None = None
    status: int | None = None


class QueryPermissionDto(ApiModel):
    keyword: str | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class AssignPermissionIdsDto(ApiModel):
    permissionIds: list[str]


# ---------------------------------------------------------------- team
class CreateTeamDto(ApiModel):
    teamName: str
    teamCode: str | None = None
    description: str | None = None
    leaderId: str | None = None
    parentId: str | None = None
    sort: int | None = None
    status: int | None = None


class UpdateTeamDto(ApiModel):
    teamName: str | None = None
    teamCode: str | None = None
    description: str | None = None
    leaderId: str | None = None
    parentId: str | None = None
    sort: int | None = None
    status: int | None = None


class QueryTeamDto(ApiModel):
    keyword: str | None = None
    status: int | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


# ---------------------------------------------------------------- document
class CreateDocumentDto(ApiModel):
    title: str
    content: str
    summary: str | None = None
    categoryId: str | None = None
    teamId: str | None = None
    coverImage: str | None = None
    tags: str | None = None
    status: int | None = Field(default=None, ge=0, le=3)
    remark: str | None = None
    isPublic: bool | None = None


class UpdateDocumentDto(ApiModel):
    """PATCH：字段均可选。"""

    title: str | None = None
    content: str | None = None
    summary: str | None = None
    categoryId: str | None = None
    teamId: str | None = None
    coverImage: str | None = None
    tags: str | None = None
    status: int | None = Field(default=None, ge=0, le=3)
    remark: str | None = None
    isPublic: bool | None = None


class QueryDocumentDto(ApiModel):
    title: str | None = None
    categoryId: str | None = None
    teamId: str | None = None
    authorId: str | None = None
    status: int | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class UploadParseDto(ApiModel):
    """upload/parse 表单可选字段（作者从 JWT 取）。"""

    categoryId: str | None = None
    teamId: str | None = None
    tags: str | None = None
    remark: str | None = None
    isPublic: bool | None = None

    @field_validator("isPublic", mode="before")
    @classmethod
    def _parse_bool(cls, value):
        # 'true'/'1' → True；'false'/'0' → False
        if isinstance(value, str):
            if value.lower() in ("true", "1"):
                return True
            if value.lower() in ("false", "0"):
                return False
        return value


class QueryReviewTasksDto(ApiModel):
    status: str | None = None  # pending | approved | rejected；默认 pending
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class ReviewDecisionDto(ApiModel):
    reviewComment: str | None = None


# ---------------------------------------------------------------- graph / search / ai
class GraphQueryDto(ApiModel):
    type: str | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)


class GraphSearchDto(ApiModel):
    keyword: str = Field(min_length=1)
    limit: int | None = Field(default=None, ge=1, le=200)


class GraphOverviewDto(ApiModel):
    keyword: str | None = None
    entityType: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    docLimit: int | None = Field(default=None, ge=1, le=80)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SearchDocumentsDto(ApiModel):
    keyword: str = Field(min_length=1)
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=50)
    categoryId: str | None = None
    authorId: str | None = None


class ChatDto(ApiModel):
    sessionId: str | None = None
    content: str = Field(min_length=1)
    topK: int = Field(default=5, ge=1, le=10)


class RagSearchDto(ApiModel):
    query: str = Field(min_length=1)
    topK: int = Field(default=5, ge=1, le=20)


class QuerySessionDto(ApiModel):
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class CreateSessionDto(ApiModel):
    title: str | None = Field(default=None, max_length=80)


class UpdateSessionDto(ApiModel):
    title: str = Field(min_length=1, max_length=80)
