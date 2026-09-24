"""文档分块服务（LangChain RecursiveCharacterTextSplitter / markdown）。

- token → 字符换算：1 token ≈ 2 字符（CHARS_PER_TOKEN=2.0）
- 分隔符：`\n# ` + markdown 语言内置分隔符
- 从块内标题行推断 heading，跨块继承并前缀补全
"""
from __future__ import annotations

import hashlib
import logging
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .types import DocumentChunk

logger = logging.getLogger("chunking")


class ChunkingService:
    _CHARS_PER_TOKEN = 2.0

    def __init__(self, chunk_size_tokens: int = 512, chunk_overlap_tokens: int = 64) -> None:
        self._heading_line = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        chunk_size = int(chunk_size_tokens * self._CHARS_PER_TOKEN)
        chunk_overlap = int(chunk_overlap_tokens * self._CHARS_PER_TOKEN)
        separators = [
            "\n# ",
            *RecursiveCharacterTextSplitter.get_separators_for_language("markdown"),
        ]
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            keep_separator=True,
            separators=separators,
        )

    async def chunk(
        self,
        content: str,
        document_id: str,
        document_title: str,
        category_id: str | None = None,
        author_id: str | None = None,
        team_id: str | None = None,
        is_public: bool | None = None,
        doc_status: int | None = None,
        publish_time: str | None = None,
    ) -> list[DocumentChunk]:
        if not (content or "").strip():
            logger.warning("文档内容为空，跳过分块：documentId=%s", document_id)
            return []

        texts = self._splitter.split_text(content)
        chunks: list[DocumentChunk] = []
        current_heading: str | None = None

        for text in texts:
            trimmed = text.strip()
            if not trimmed:
                continue

            heading_in_chunk = self._extract_heading(trimmed)
            if heading_in_chunk:
                current_heading = heading_in_chunk

            chunk_content = trimmed
            if current_heading and not self._heading_line.search(trimmed):
                chunk_content = f"{current_heading}\n\n{trimmed}"

            chunk_id = hashlib.sha256(f"{document_id}:{len(chunks)}".encode("utf-8")).hexdigest()[:64]
            chunks.append(
                DocumentChunk(
                    chunkId=chunk_id,
                    documentId=document_id,
                    documentTitle=document_title,
                    content=chunk_content,
                    heading=current_heading,
                    chunkIndex=len(chunks),
                    totalChunks=0,
                    categoryId=category_id,
                    authorId=author_id,
                    teamId=team_id,
                    isPublic=is_public,
                    docStatus=doc_status,
                    publishTime=publish_time,
                )
            )

        total = len(chunks)
        for c in chunks:
            c.totalChunks = total

        logger.debug("文档分块完成：documentId=%s, totalChunks=%s", document_id, total)
        return chunks

    def _extract_heading(self, text: str) -> str | None:
        match = self._heading_line.search(text)
        if not match:
            return None
        return match.group(2).strip() or None
