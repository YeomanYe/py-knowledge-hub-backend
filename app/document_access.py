"""文档访问控制：当前用户可读/可写哪些文档，以及 ES / Neo4j 可见性构造。

可见范围：公开 ∪ 所在团队 ∪ 自己写的；管理员/审核员不限制。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import RoleCode

# 文档状态常量（与 models.DocumentStatus 保持一致，避免循环导入）
_DRAFT = 0
_PUBLISHED = 1


@dataclass
class DocumentAccessScope:
    unrestricted: bool
    userId: str
    teamIds: list[str] = field(default_factory=list)


def access_from_user(user: dict | None) -> DocumentAccessScope:
    """从 AuthUser（dict）构造访问范围；未登录视为普通用户（无 team）。"""
    if user is None:
        return DocumentAccessScope(unrestricted=False, userId="", teamIds=[])
    roles = user.get("roles") or []
    return DocumentAccessScope(
        unrestricted=RoleCode.ADMIN in roles or RoleCode.REVIEWER in roles,
        userId=str(user.get("userId") or ""),
        teamIds=[str(t) for t in (user.get("teamIds") or [])],
    )


def can_read_document(doc: dict, scope: DocumentAccessScope) -> bool:
    """doc：Document ORM 对象或 dict（含 author_id/team_id/is_public/status）。"""
    author_id = _get(doc, "author_id") or _get(doc, "authorId")
    team_id = _get(doc, "team_id") or _get(doc, "teamId")
    is_public = bool(_get(doc, "is_public") or _get(doc, "isPublic"))
    status = _get(doc, "status")

    if scope.unrestricted:
        return True
    if author_id and str(author_id) == scope.userId:
        return True
    if status is not None and int(status) != _PUBLISHED:
        return False
    if is_public:
        return True
    return bool(team_id and str(team_id) in scope.teamIds)


def can_write_document(doc: dict, user: dict) -> bool:
    """只有管理员或作者本人可写。"""
    roles = user.get("roles") or []
    if RoleCode.ADMIN in roles:
        return True
    author_id = _get(doc, "author_id") or _get(doc, "authorId")
    return bool(author_id and str(author_id) == str(user.get("userId") or ""))


# ---------------------------------------------------------------- Elasticsearch
ES_CHUNK_VISIBILITY_FIELDS = {
    "isPublic": "is_public",
    "authorId": "author_id",
    "teamId": "team_id",
}

ES_DOC_VISIBILITY_FIELDS = {
    "isPublic": "isPublic",
    "authorId": "authorId",
    "teamId": "teamId",
}


def es_visibility_filter(
    scope: DocumentAccessScope,
    fields: dict[str, str],
) -> dict[str, Any] | None:
    """ES 可见性 filter（只筛不打分）。管理员/审核员返回 None，调用方不加过滤。"""
    if scope.unrestricted:
        return None
    should: list[dict[str, Any]] = [
        {"term": {fields["isPublic"]: True}},
        {"term": {fields["authorId"]: scope.userId}},
    ]
    if scope.teamIds:
        should.append({"terms": {fields["teamId"]: scope.teamIds}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


def wrap_es_query(
    query: dict[str, Any], filter_: dict[str, Any] | None
) -> dict[str, Any]:
    """相关性查询放 must，可见性放 filter，避免权限条件影响打分。"""
    if not filter_:
        return query
    return {"bool": {"must": [query], "filter": [filter_]}}


# ---------------------------------------------------------------- Neo4j
def neo4j_access_params(scope: DocumentAccessScope | None) -> dict[str, Any]:
    return {
        "unrestricted": not scope or scope.unrestricted,
        "accessUserId": (scope.userId if scope else "") or "",
        "accessTeamIds": (scope.teamIds if scope else []) or [],
    }


def neo4j_document_access_where(alias: str = "d") -> str:
    """Cypher：文档节点是否对当前用户可见。"""
    return (
        f"($unrestricted OR {alias}.authorId = $accessUserId "
        f"OR {alias}.isPublic = true OR {alias}.teamId IN $accessTeamIds)"
    )


def _get(doc, key: str) -> Any:
    if isinstance(doc, dict):
        return doc.get(key)
    return getattr(doc, key, None)
