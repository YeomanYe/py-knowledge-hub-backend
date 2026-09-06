"""FastAPI 应用入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .container import Container
from .routers import ai, auth, documents, graph, permissions, roles, search, teams, users

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = Container.instance()
    # 先注册消费者，再启动 RabbitMQ（bindConsumers 需要 handlers 已注册）
    container.pipeline_consumer()
    try:
        await container.rabbit().start()
    except Exception:
        # 与 NestJS onModuleInit 一致：MQ 不可用时启动失败
        raise
    yield
    await container.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Knowledge Hub Backend (Python/FastAPI)",
        version="1.0.0",
        lifespan=lifespan,
    )

    # 启用 CORS（任意来源、携带凭证）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(roles.router)
    app.include_router(permissions.router)
    # 公开 tree 先注册，避免被 /teams/{team_id} 抢占
    app.include_router(teams.public_router)
    app.include_router(teams.router)
    app.include_router(documents.router)
    app.include_router(search.router)
    app.include_router(ai.router)
    app.include_router(graph.router)

    @app.get("/")
    async def root():
        # GET / → 'Hello World!'
        return "Hello World!"

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
