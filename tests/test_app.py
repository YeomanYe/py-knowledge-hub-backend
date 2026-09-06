"""应用装配冒烟测试：路由注册与鉴权依赖（不连外部服务）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def _collect_paths(routes) -> set[str]:
    paths: set[str] = set()
    for route in routes:
        if hasattr(route, "path"):
            paths.add(route.path)
        elif hasattr(route, "original_router"):
            paths |= _collect_paths(route.original_router.routes)
        elif hasattr(route, "routes"):
            paths |= _collect_paths(route.routes)
    return paths


def test_routes_registered():
    paths = _collect_paths(app.routes)
    expected = {
        "/auth/register",
        "/auth/login",
        "/auth/refresh",
        "/auth/verify-email",
        "/auth/me",
        "/auth/reviewer-ids",
        "/users/me",
        "/users/page",
        "/roles/list",
        "/permissions/tree",
        "/teams/tree",
        "/teams/page",
        "/documents",
        "/documents/upload/parse",
        "/documents/reviews/tasks",
        "/documents/reviews/tasks/pending-count",
        "/documents/{document_id}/publish",
        "/search",
        "/rag/search",
        "/ai/chat",
        "/ai/sessions",
        "/ai/sessions/{session_id}/messages",
        "/graph/overview",
        "/graph/search",
        "/graph/nodes",
        "/graph/edges",
    }
    for path in expected:
        assert path in paths, f"缺少路由: {path}"


def test_public_login_requires_no_auth():
    with TestClient(app) as client:
        # 无 token 访问公开接口 → 业务参数校验 422，而不是 401
        response = client.post("/auth/login", json={})
        assert response.status_code == 422


def test_protected_route_401():
    with TestClient(app) as client:
        response = client.get("/auth/me")
        assert response.status_code == 401


def test_teams_tree_public():
    with TestClient(app) as client:
        response = client.get("/teams/tree")
        # 公开路由无需登录：数据库不可用时内部报错也是 500/服务异常，而非 401
        assert response.status_code != 401
