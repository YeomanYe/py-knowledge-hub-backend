# py-knowledge-hub-backend

**Python + FastAPI** 实现的知识库管理系统后端，含文档中心、用户/角色/权限、团队、发布审核流、全文搜索（Elasticsearch）、RAG 混合检索（向量 + 关键词 + RRF + 重排）、知识图谱（Neo4j）、AI 会话（OpenAI 兼容）、RabbitMQ 异步管线。

## 技术映射

| NestJS/TS 版 | Python 版 |
| --- | --- |
| NestJS Controller / Guards | FastAPI Router / dependencies |
| Passport JWT | PyJWT（HS256，access 2h / refresh 7d） |
| TypeORM + Postgres | SQLAlchemy 2 async + asyncpg |
| Mongoose + MongoDB | motor |
| @aws-sdk/client-s3 | boto3（RustFS / MinIO S3 兼容） |
| @elastic/elasticsearch | elasticsearch-py（异步） |
| neo4j-driver | neo4j 官方驱动（异步） |
| amqp-connection-manager | aio-pika |
| @nestjs/mailer + nodemailer | aiosmtplib |
| LangChain OpenAI / RecursiveCharacterTextSplitter | openai（AsyncOpenAI）/ langchain-text-splitters |
| class-validator | Pydantic（`extra="forbid"`） |
| snowflake-id | 自实现雪花 ID |

## 目录结构

```
app/
├── main.py              # FastAPI 入口（CORS、lifespan、路由装配、/health）
├── config.py            # pydantic-settings 配置（.env）
├── database.py          # Postgres(asyncpg) + Mongo(motor) + 请求级 session（成功自动 commit）
├── models.py            # 12 张表 SQLAlchemy 模型（eager_defaults 避免惰性刷新）
├── mongo_models.py      # document_content 集合（版本号自增）
├── common.py            # 雪花 ID、权限/角色常量
├── schemas.py           # 全量 DTO（extra=forbid）
├── container.py         # DI 懒加载单例 + lifespan 关闭
├── deps.py              # get_current_user / require_permission / require_roles
├── redis_service.py     # 激活 token / 重置验证码
├── storage.py           # RustFS 上传（to_thread 封装 S3）
├── pipeline/            # 分块 / 嵌入 / 抽取 / ES 向量与全文索引 / Neo4j 建图 / 编排
├── mq/                  # RabbitMQ 拓扑、消费者、发布者（RAG / Search / KG 三队列）
├── ai/                  # AI 会话、混合检索、Rerank、引用来源
├── parsers/             # pdf / docx / pptx / xlsx / txt / md 解析为 Markdown
├── services/            # 用户 / 角色 / 权限 / 团队 / 鉴权 / 文档 / 审核 / 邮件 / 激活 / 重置密码
└── routers/             # auth / users / roles / permissions / teams / documents / search / ai / graph
```

## 快速开始

前置：本机已启动对应 docker 服务（Postgres / MongoDB / Redis / RabbitMQ / Elasticsearch / Neo4j / RustFS），并已用 `init-scripts/` 初始化数据（含种子账号）。

```bash
# 1. 安装（Python 3.12+；本仓库在 3.12.13 验证）
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 2. 配置（可复制 .env.example 为 .env 并按需修改；默认值即直连本地 docker）
cp .env.example .env

# 3. 启动（默认 0.0.0.0:3000，局域网可访问；仅本机调试可改 --host 127.0.0.1）
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 3000

# 4. 测试（单元 + 直连本地 docker 的集成测试，共 38 项）
.venv/bin/python -m pytest tests/ -q
```

## 种子账号（init-scripts/postgresql/init.sql，密码均为 123456）

| 账号 | 角色 | 说明 |
| --- | --- | --- |
| admin | ROLE_ADMIN + ROLE_REVIEWER | 系统管理员（自动放行全部权限） |
| reviewer | ROLE_REVIEWER | 文档审核员 |
| user | ROLE_USER | 普通用户 |

## 关键行为

- **JWT**：payload `{sub, username, type}`，access 2h / refresh 7d，`/auth/refresh` 校验 type=refresh。
- **文档状态机**：Draft=0 / Published=1 / Archived=2 / PendingReview=3。
- **发布审核**：`DOCUMENT_REQUIRE_APPROVAL=true`（默认）时，发布 → 待审 → 审核员通过/驳回；驳回必须填写意见。
- **RAG**：CHUNK_SIZE=512 / OVERLAP=64，embedding 1024 维（DashScope 兼容），ES `kh_chunk`（dense_vector + IK），关键词 + 向量 RRF（C=60）+ rerank；未配置 API Key 时自动降级为纯关键词（不报错）。
- **知识图谱**：`(KnowledgeDocument)-[:HAS_CHUNK]->(DocumentChunk)-[:MENTIONS]->(KnowledgeEntity)`，未配 LLM Key 时跳过实体抽取（删除/图谱查询仍可用）。
- **MQ**：交换 `rag.reindex.exchange` / `search.index.exchange` / `kg.graph.exchange`，队列 `kh.rag.reindex.queue` / `kh.search.index.queue` / `kh.kg.graph.queue`；发布/下架后异步重建索引与图谱，投递失败只记日志不影响文档状态。
- **ES 客户端版本**：容器 ES 为 8.x，依赖锁定 `elasticsearch>=8.17,<9`（9.x 客户端会因 Accept 版本协商被 8.x 服务拒绝）。
- **原文件上传**：RustFS（S3）路径 `documents/YYYY/MM/DD/<安全名>-<uuid>.<ext>`。
- **未登录不落库**：AI 聊天在未登录时正常返回但不写会话记录。

## 接口速览

- `POST /auth/register|login|refresh`、`GET /auth/me`、`POST /auth/logout`、`GET /auth/reviewer-ids`
- `POST /documents`、`PUT /documents/{id}`、`PATCH /documents/{id}`、`DELETE /documents/{id}`、`POST /documents/upload/parse`
- `PUT /documents/{id}/publish|archive|restore`、`GET /documents/reviews/tasks|pending-count`、`POST /documents/reviews/tasks/{id}/approve|reject`
- `GET /documents/page`、`GET /teams/tree`（公开）、`POST /search`、`POST /rag/search`、`GET /graph/overview`
- `POST /ai/sessions`、`GET/PATCH/DELETE /ai/sessions/{id}`、`POST /ai/sessions/{id}/messages`
- 管理端：`/users/page`、`/roles/list`、`/permissions/tree`、`/teams/page`（需 admin）

启动后可在 `http://127.0.0.1:8000/docs` 查看 OpenAPI 文档。

## 备注

- 与其他后端实现同时运行时，会竞争同一批 RabbitMQ 队列（round-robin）；请勿同时运行两套后端。
- 集成测试会在本地 docker 中写入测试数据（用户名以 `it_`/`es_verify*` 开头）。
