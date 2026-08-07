"""MongoDB 文档正文存取。

集合：document_content
- `_id`：ObjectId，映射 kh_document.content_id
- `documentId`：↔ kh_document.id（唯一索引）
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

COLLECTION = "document_content"


def new_content_id() -> str:
    """生成 content_id（前端生成 ObjectId 字符串；这里用 UUID hex 保证唯一）。"""
    return uuid.uuid4().hex


class DocumentContentRepo:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._col = db[COLLECTION]

    async def create(
        self,
        content_id: str,
        document_id: str,
        content: str,
        content_length: int,
        content_summary: str | None,
    ) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        await self._col.insert_one(
            {
                "_id": content_id,
                "documentId": document_id,
                "content": content,
                "contentLength": content_length,
                "contentSummary": content_summary,
                "version": 1,
                "deleted": False,
                "createdAt": now,
                "updatedAt": now,
            }
        )
        return content_id

    async def find_by_document_id(self, document_id: str) -> dict[str, Any] | None:
        return await self._col.find_one({"documentId": document_id, "deleted": False})

    async def find_by_id(self, content_id: str) -> dict[str, Any] | None:
        return await self._col.find_one({"_id": content_id, "deleted": False})

    async def update_content(
        self, content_id: str, content: str, content_length: int,
        content_summary: str | None,
    ) -> bool:
        result = await self._col.update_one(
            {"_id": content_id},
            {
                "$set": {
                    "content": content,
                    "contentLength": content_length,
                    "contentSummary": content_summary,
                    "updatedAt": datetime.datetime.now(datetime.timezone.utc),
                },
                "$inc": {"version": 1},
            },
        )
        return result.modified_count > 0

    async def mark_deleted_by_document_id(self, document_id: str) -> None:
        await self._col.update_many(
            {"documentId": document_id},
            {"$set": {"deleted": True, "updatedAt": datetime.datetime.now(datetime.timezone.utc)}},
        )

    async def hard_delete_by_document_id(self, document_id: str) -> None:
        await self._col.delete_many({"documentId": document_id})
