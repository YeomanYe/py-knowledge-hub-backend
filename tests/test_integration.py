"""端到端集成测试：直连本地 docker 服务（Postgres/Mongo/Redis/RabbitMQ/ES/Neo4j）。

要求：
- docker-compose 服务已启动（用户本地已启动）
- 数据库已由 init-scripts/postgresql/init.sql 初始化（含种子账号 admin/reviewer/user，密码 123456）

说明：DOCUMENT_REQUIRE_APPROVAL 默认 true，发布走审核流。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

TS = str(int(time.time() * 1000))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _register_and_login(client: TestClient, username: str) -> dict:
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "123456", "realName": "集成测试"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    # REQUIRE_EMAIL_VERIFICATION=false 时无 emailVerificationRequired 字段
    assert data["message"] == "注册成功，请登录"

    login = client.post("/auth/login", json={"username": username, "password": "123456"})
    assert login.status_code == 200, login.text
    return login.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_full_document_workflow(client: TestClient):
    """注册 → 登录 → me → 建文档 → 发布(待审) → reviewer 审核通过 → 详情含 Mongo 正文。"""
    auth = _register_and_login(client, f"it_user_{TS}")
    token = auth["accessToken"]
    headers = _auth_headers(token)

    # me（AuthUser：userId 字段）
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    user_id = me.json()["userId"]

    # 权限内可建文档
    title = f"集成测试文档 {TS}"
    create = client.post(
        "/documents",
        headers=headers,
        json={
            "title": title,
            "content": "# 第一章\n\n这是集成测试正文。\n\n## 小节\n\n更多内容。",
            "summary": "测试摘要",
            "tags": "test,it",
        },
    )
    assert create.status_code == 200, create.text
    doc = create.json()
    doc_id = doc["id"]
    assert doc["status"] == 0  # Draft
    assert doc["authorId"] == user_id

    # 发布 → 开启审核 → PendingReview(3)
    publish = client.put(f"/documents/{doc_id}/publish", headers=headers)
    assert publish.status_code == 200, publish.text
    assert publish.json()["status"] == 3  # PendingReview

    # 审核中不可编辑
    patch = client.patch(
        f"/documents/{doc_id}", headers=headers, json={"title": "改名"}
    )
    assert patch.status_code == 400

    # reviewer 登录 → 待办 → 通过
    reviewer = client.post(
        "/auth/login", json={"username": "reviewer", "password": "123456"}
    )
    assert reviewer.status_code == 200, reviewer.text
    reviewer_headers = _auth_headers(reviewer.json()["accessToken"])

    tasks = client.get("/documents/reviews/tasks", headers=reviewer_headers)
    assert tasks.status_code == 200, tasks.text
    pending = [t for t in tasks.json()["items"] if t["documentId"] == doc_id]
    assert pending, "待审列表应包含刚提交的文档"
    task_id = pending[0]["id"]

    approve = client.post(
        f"/documents/reviews/tasks/{task_id}/approve",
        headers=reviewer_headers,
        json={"reviewComment": "内容合格"},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == 1  # Published

    # 详情含 Mongo 正文
    detail = client.get(f"/documents/{doc_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert "集成测试正文" in detail.json()["content"]

    # 待审核数
    pending_count = client.get(
        "/documents/reviews/tasks/pending-count", headers=reviewer_headers
    )
    assert pending_count.status_code == 200


def test_review_reject_flow(client: TestClient):
    """驳回 → 回 Draft → 可再编辑。"""
    auth = _register_and_login(client, f"it_reject_{TS}")
    headers = _auth_headers(auth["accessToken"])

    create = client.post(
        "/documents",
        headers=headers,
        json={"title": f"驳回测试 {TS}", "content": "会被驳回的正文"},
    )
    doc_id = create.json()["id"]
    client.put(f"/documents/{doc_id}/publish", headers=headers)

    reviewer = client.post(
        "/auth/login", json={"username": "reviewer", "password": "123456"}
    )
    reviewer_headers = _auth_headers(reviewer.json()["accessToken"])
    tasks = client.get("/documents/reviews/tasks", headers=reviewer_headers).json()
    task_id = next(t["id"] for t in tasks["items"] if t["documentId"] == doc_id)

    reject = client.post(
        f"/documents/reviews/tasks/{task_id}/reject",
        headers=reviewer_headers,
        json={"reviewComment": "需要补充细节"},
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == 0  # 回 Draft

    # 驳回意见为空 → 400
    create2 = client.post(
        "/documents", headers=headers, json={"title": f"驳回空意见 {TS}", "content": "x"}
    ).json()
    client.put(f"/documents/{create2['id']}/publish", headers=headers)
    tasks2 = client.get("/documents/reviews/tasks", headers=reviewer_headers).json()
    task2 = next(t["id"] for t in tasks2["items"] if t["documentId"] == create2["id"])
    empty = client.post(
        f"/documents/reviews/tasks/{task2}/reject", headers=reviewer_headers, json={}
    )
    assert empty.status_code == 400


def test_upload_parse_txt(client: TestClient):
    """上传 txt 解析建稿。"""
    auth = _register_and_login(client, f"it_upload_{TS}")
    headers = _auth_headers(auth["accessToken"])

    files = {"file": ("说明.md", "## 段落\n\n这是上传解析内容。".encode("utf-8"), "text/markdown")}
    data = {"tags": "upload", "isPublic": "true"}
    response = client.post("/documents/upload/parse", headers=headers, files=files, data=data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fileExtension"] == "md"
    assert "上传解析内容" in body["contentPreview"]
    assert body["status"] == 0


def test_rag_search_es_connected(client: TestClient):
    """RAG 混合检索（无 API Key 时降级仅关键词，ES 可达返回 200）。"""
    auth = _register_and_login(client, f"it_rag_{TS}")
    headers = _auth_headers(auth["accessToken"])
    response = client.post(
        "/rag/search", headers=headers, json={"query": "测试", "topK": 5}
    )
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


def test_search_documents(client: TestClient):
    auth = _register_and_login(client, f"it_search_{TS}")
    headers = _auth_headers(auth["accessToken"])
    response = client.post(
        "/search", headers=headers, json={"keyword": "集成测试", "page": 1, "pageSize": 10}
    )
    assert response.status_code == 200, response.text
    assert "items" in response.json()


def test_graph_overview(client: TestClient):
    auth = _register_and_login(client, f"it_graph_{TS}")
    headers = _auth_headers(auth["accessToken"])
    response = client.get("/graph/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "nodes" in body and "edges" in body and "stats" in body


def test_ai_sessions_crud(client: TestClient):
    auth = _register_and_login(client, f"it_ai_{TS}")
    headers = _auth_headers(auth["accessToken"])

    create = client.post("/ai/sessions", headers=headers, json={"title": "测试会话"})
    assert create.status_code == 200, create.text
    session_id = create.json()["id"]

    rename = client.patch(
        f"/ai/sessions/{session_id}", headers=headers, json={"title": "改名会话"}
    )
    assert rename.status_code == 200
    assert rename.json()["title"] == "改名会话"

    messages = client.get(f"/ai/sessions/{session_id}/messages", headers=headers)
    assert messages.status_code == 200
    assert messages.json() == []

    remove = client.delete(f"/ai/sessions/{session_id}", headers=headers)
    assert remove.status_code == 200
    assert remove.json() == {"message": "已删除"}


def test_admin_user_management(client: TestClient):
    """管理员用户管理接口。"""
    admin = client.post("/auth/login", json={"username": "admin", "password": "123456"})
    assert admin.status_code == 200, admin.text
    headers = _auth_headers(admin.json()["accessToken"])

    page = client.get("/users/page", headers=headers, params={"page": 1, "pageSize": 5})
    assert page.status_code == 200, page.text
    assert "items" in page.json()

    roles = client.get("/roles/list", headers=headers)
    assert roles.status_code == 200
    role_codes = [r["roleCode"] for r in roles.json()]
    assert "ROLE_ADMIN" in role_codes and "ROLE_REVIEWER" in role_codes

    perms = client.get("/permissions/tree", headers=headers)
    assert perms.status_code == 200
    assert isinstance(perms.json(), list)

    teams = client.get("/teams/page", headers=headers, params={"page": 1, "pageSize": 5})
    assert teams.status_code == 200

    reviewer_ids = client.get("/auth/reviewer-ids", headers=headers)
    assert reviewer_ids.status_code == 200
    assert isinstance(reviewer_ids.json(), list)


def test_permission_denied_for_normal_user(client: TestClient):
    """普通用户访问管理员接口 → 403。"""
    auth = _register_and_login(client, f"it_denied_{TS}")
    headers = _auth_headers(auth["accessToken"])
    response = client.get("/users/page", headers=headers)
    assert response.status_code == 403


def test_teams_tree_public_data(client: TestClient):
    response = client.get("/teams/tree")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
