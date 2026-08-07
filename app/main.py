"""FastAPI 应用入口。"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import documents

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Knowledge Hub Backend (Python/FastAPI)",
        version="1.0.0",
    )

    # 启用 CORS（任意来源、携带凭证）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(documents.router)

    @app.get("/")
    async def root():
        # GET / → 'Hello World!'
        return "Hello World!"

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
