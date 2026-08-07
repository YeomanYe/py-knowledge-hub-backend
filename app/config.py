"""应用配置。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 服务
    port: int = 3000

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "user"
    postgres_password: str = "123456"
    postgres_db: str = "knowledge_hub"

    # MongoDB
    mongo_uri: str = (
        "mongodb://mongo_user:mongo_pass123@localhost:27017/knowledge_hub?authSource=admin"
    )

    # Snowflake
    snowflake_worker_id: int = 1
    snowflake_offset: int = 1704067200000

    # RustFS（S3 兼容）
    rustfs_enabled: bool = True
    rustfs_endpoint: str = "http://localhost:9000"
    rustfs_public_url: str = "http://localhost:9000"
    rustfs_access_key: str = "rustfsadmin"
    rustfs_secret_key: str = "rustfsadmin"
    rustfs_bucket: str = "knowledge-hub"
    rustfs_region: str = "us-east-1"

    # RabbitMQ
    rabbitmq_enabled: bool = True
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672"
    rabbitmq_connect_timeout_ms: int = 15000

    # RAG 分块
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64

    # Embedding（OpenAI 兼容）
    embedding_dimension: int = 1024
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v3"
    embedding_batch_size: int = 10
    embedding_api_key: str | None = None
    dashscope_api_key: str | None = None

    # RAG 混合检索
    rag_hybrid_top_k: int = 20
    rag_rrf_c: int = 60
    rag_rerank_enabled: bool = True
    rag_rerank_model: str = "qwen3-rerank"
    rerank_api_key: str | None = None
    rerank_base_url: str = "https://dashscope.aliyuncs.com"
    ai_chat_timeout_ms: int = 60000

    # LLM（OpenAI 兼容）
    openai_api_key: str | None = None
    llm_api_key: str | None = None
    openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_base_url: str | None = None
    model_name: str = "qwen-plus"
    llm_model: str | None = None
    kg_llm_timeout_ms: int = 120000
    kg_max_entities: int = 12
    kg_max_relations: int = 15

    # Elasticsearch
    elasticsearch_enabled: bool = True
    elasticsearch_node: str = "http://localhost:9200"

    # Neo4j
    neo4j_enabled: bool = True
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "12345678"

    # JWT
    jwt_secret: str = "dev-secret-change-me-in-production"
    jwt_access_expires: str = "2h"
    jwt_refresh_expires: str = "7d"

    # 邮箱验证
    require_email_verification: bool = False
    app_public_url: str = "http://localhost:3000"

    # SMTP 邮件
    mail_host: str = "smtp.qq.com"
    mail_port: int = 587
    mail_secure: bool = False
    mail_user: str = "xx@xx.com"
    mail_pass: str = "xx"
    mail_from: str = "Knowledge Hub <xx@xx.com>"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_db: int = 0

    # 文档发布审核
    document_require_approval: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
