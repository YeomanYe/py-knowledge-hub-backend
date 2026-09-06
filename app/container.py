"""轻量服务容器（各 Service 懒加载单例）。"""
from __future__ import annotations

from typing import Any, Callable

from .database import SessionLocal, get_mongo
from .mongo_models import DocumentContentRepo
from .mq.consumer import DocumentPipelineConsumer
from .mq.publisher import DocumentPipelinePublisher
from .mq.rabbitmq import RabbitMqService
from .parsers.file_parser import FileParserService
from .pipeline.chunking import ChunkingService
from .pipeline.embedding import EmbeddingService
from .pipeline.extraction import ExtractionService
from .pipeline.graph_build import GraphBuildService
from .pipeline.orchestrator import PipelineOrchestrator
from .pipeline.search_index import SearchIndexService
from .pipeline.vector_index import VectorIndexService
from .redis_service import RedisService
from .storage import RustfsService


class Container:
    _instance: "Container | None" = None

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    @classmethod
    def instance(cls) -> "Container":
        if cls._instance is None:
            cls._instance = Container()
        return cls._instance

    def _get(self, key: str, factory: Callable[[], Any]) -> Any:
        if key not in self._services:
            self._services[key] = factory()
        return self._services[key]

    # ------------------------------------------------------------ 基础
    def redis(self) -> RedisService:
        return self._get("redis", RedisService)

    def rustfs(self) -> RustfsService:
        return self._get("rustfs", RustfsService)

    def content_repo(self) -> DocumentContentRepo:
        return self._get("content_repo", lambda: DocumentContentRepo(get_mongo()))

    # ------------------------------------------------------------ pipeline
    def chunking_service(self) -> ChunkingService:
        return self._get(
            "chunking",
            lambda: ChunkingService(
                chunk_size_tokens=512, chunk_overlap_tokens=64
            ),
        )

    def embedding_service(self) -> EmbeddingService:
        return self._get("embedding", EmbeddingService)

    def extraction_service(self) -> ExtractionService:
        return self._get("extraction", ExtractionService)

    def vector_index_service(self) -> VectorIndexService:
        return self._get("vector_index", VectorIndexService)

    def search_index_service(self) -> SearchIndexService:
        return self._get("search_index", SearchIndexService)

    def graph_build_service(self) -> GraphBuildService:
        return self._get(
            "graph_build",
            lambda: GraphBuildService(
                self.chunking_service(), self.extraction_service()
            ),
        )

    def orchestrator(self) -> PipelineOrchestrator:
        return self._get(
            "orchestrator",
            lambda: PipelineOrchestrator(
                session_factory=SessionLocal,
                content_repo=self.content_repo(),
                chunking_service=self.chunking_service(),
                embedding_service=self.embedding_service(),
                vector_index_service=self.vector_index_service(),
                search_index_service=self.search_index_service(),
                graph_build_service=self.graph_build_service(),
            ),
        )

    # ------------------------------------------------------------ mq
    def rabbit(self) -> RabbitMqService:
        return self._get("rabbit", RabbitMqService)

    def pipeline_publisher(self) -> DocumentPipelinePublisher:
        return self._get(
            "pipeline_publisher",
            lambda: DocumentPipelinePublisher(self.rabbit()),
        )

    def pipeline_consumer(self) -> DocumentPipelineConsumer:
        return self._get(
            "pipeline_consumer",
            lambda: DocumentPipelineConsumer(self.rabbit(), self.orchestrator()),
        )

    # ------------------------------------------------------------ services
    def user_service(self):
        from .services.user_service import UserService

        return self._get("user_service", lambda: UserService(self.permission_service()))

    def permission_service(self):
        from .services.permission_service import PermissionService

        return self._get("permission_service", PermissionService)

    def role_service(self):
        from .services.role_service import RoleService

        return self._get("role_service", RoleService)

    def team_service(self):
        from .services.team_service import TeamService

        return self._get("team_service", TeamService)

    def review_service(self):
        from .services.document_review_service import DocumentReviewService

        return self._get(
            "review_service",
            lambda: DocumentReviewService(self.pipeline_publisher()),
        )

    def document_service(self):
        from .services.document_service import DocumentService

        return self._get(
            "document_service",
            lambda: DocumentService(
                content_repo=self.content_repo(),
                file_parser_service=FileParserService(self.rustfs()),
                rustfs=self.rustfs(),
                pipeline_publisher=self.pipeline_publisher(),
                review_service=self.review_service(),
            ),
        )

    def auth_service(self):
        from .services.auth_service import AuthService
        from .services.email_activation import EmailActivationService
        from .services.email_service import EmailService
        from .services.password_reset import PasswordResetService

        return self._get(
            "auth_service",
            lambda: AuthService(
                user_service=self.user_service(),
                email_service=EmailService(),
                email_activation=EmailActivationService(self.redis()),
                password_reset=PasswordResetService(self.redis()),
            ),
        )

    # ------------------------------------------------------------ ai
    def hybrid_retrieval(self):
        from .ai.hybrid_retrieval import HybridRetrievalService
        from .ai.reranker import RerankerService

        return self._get(
            "hybrid_retrieval",
            lambda: HybridRetrievalService(
                embedding=self.embedding_service(),
                vector_index=self.vector_index_service(),
                reranker=RerankerService(),
            ),
        )

    def chat_sessions(self):
        from .ai.chat_session import ChatSessionService

        return self._get("chat_sessions", lambda: ChatSessionService(SessionLocal))

    def ai_chat(self):
        from .ai.ai_chat import AiChatService

        return self._get(
            "ai_chat",
            lambda: AiChatService(self.hybrid_retrieval(), self.chat_sessions()),
        )

    # ------------------------------------------------------------ 生命周期
    async def close(self) -> None:
        from .database import close_mongo, dispose_engine

        if "rabbit" in self._services:
            await self.rabbit().stop()
        if "vector_index" in self._services:
            await self.vector_index_service().close()
        if "search_index" in self._services:
            await self.search_index_service().close()
        if "graph_build" in self._services:
            await self.graph_build_service().close()
        if "redis" in self._services:
            await self.redis().close()
        await close_mongo()
        await dispose_engine()
        # 释放单例：下一个 lifespan 可重建全新容器（生产单进程单生命周期，等价于进程退出）
        Container._instance = None
