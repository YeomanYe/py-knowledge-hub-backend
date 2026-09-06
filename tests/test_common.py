"""公共模块与管线单元测试（不依赖外部服务）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.common import PermissionCode, SnowflakeId, next_snowflake_id
from app.pipeline.chunking import ChunkingService
from app.pipeline.kg_schema import (
    build_extraction_system_prompt,
    normalize_entity_type,
    normalize_relation_type,
)
from app.pipeline.vector_index import VectorIndexService
from app.schemas import (
    CreateUserDto,
    GraphOverviewDto,
    LoginDto,
    UploadParseDto,
)


class TestSnowflake:
    def test_generate_str_and_incremental(self):
        a = next_snowflake_id()
        b = next_snowflake_id()
        assert isinstance(a, str)
        assert int(a) < int(b)

    def test_worker_bits(self):
        sf = SnowflakeId(mid=0)
        sf2 = SnowflakeId(mid=1023)
        v = int(sf.generate())
        assert v > 0


class TestKgSchema:
    def test_normalize(self):
        assert normalize_entity_type("person") == "PERSON"
        assert normalize_entity_type("未知") == "CONCEPT"
        assert normalize_relation_type("uses") == "USES"
        assert normalize_relation_type("未知") == "RELATED_TO"

    def test_prompt_contains_limits(self):
        prompt = build_extraction_system_prompt(12, 15)
        assert "12 个实体" in prompt
        assert "15 个关系" in prompt


class TestChunking:
    async def test_chunk_long_text(self):
        service = ChunkingService(chunk_size_tokens=32, chunk_overlap_tokens=4)
        content = "\n".join(f"# 章节{i}\n" + f"这是第 {i} 章的内容。" * 40 for i in range(3))
        chunks = await service.chunk(
            content, document_id="doc-1", document_title="测试文档"
        )
        assert len(chunks) >= 1
        assert all(c.documentId == "doc-1" for c in chunks)
        assert all(len(c.chunkId) == 64 for c in chunks)
        assert chunks[0].chunkIndex == 0

    async def test_empty_content(self):
        service = ChunkingService()
        assert await service.chunk("", "d", "t") == []


class TestSchemas:
    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            CreateUserDto.model_validate(
                {"username": "a", "password": "123456", "hack": 1}
            )

    def test_login_required_fields(self):
        with pytest.raises(ValidationError):
            LoginDto.model_validate({"username": "a"})

    def test_upload_is_public_bool(self):
        dto = UploadParseDto.model_validate(
            {"isPublic": "true", "categoryId": "c1"}
        )
        assert dto.isPublic is True
        dto2 = UploadParseDto.model_validate({"isPublic": "0"})
        assert dto2.isPublic is False

    def test_graph_from_alias(self):
        dto = GraphOverviewDto.model_validate({"from": "2026-01-01", "to": "2026-12-31"})
        assert dto.from_ == "2026-01-01"


class TestVectorIndex:
    def test_rrf_fuse(self):
        from app.pipeline.types import ChunkHit

        service = VectorIndexService.__new__(VectorIndexService)
        kw = [
            ChunkHit("c1", "d1", "t", "x", None, 5.0),
            ChunkHit("c2", "d1", "t", "y", None, 4.0),
        ]
        vec = [
            ChunkHit("c2", "d1", "t", "y", None, 6.0),
            ChunkHit("c3", "d1", "t", "z", None, 5.0),
        ]
        fused = service._rrf_fuse(kw, vec, 60)
        assert len(fused) == 3
        assert fused[0].chunkId == "c2"
        assert fused[0].bm25Score == 4.0
        assert fused[0].vectorScore == 6.0
