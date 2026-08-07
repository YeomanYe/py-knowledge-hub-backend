"""RustFS 文件存储（S3 兼容，）。"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
import uuid

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException

from .config import get_settings

logger = logging.getLogger("rustfs")


class RustfsService:
    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = settings.rustfs_enabled
        self._bucket = ""
        self._public_base_url = ""
        self._client = None

        if not self._enabled:
            logger.warning("RustFS 已禁用（RUSTFS_ENABLED=false），文件上传将跳过")
            return

        endpoint = settings.rustfs_endpoint
        self._bucket = settings.rustfs_bucket
        self._public_base_url = (settings.rustfs_public_url or endpoint).rstrip("/")

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=settings.rustfs_region,
            aws_access_key_id=settings.rustfs_access_key,
            aws_secret_access_key=settings.rustfs_secret_key,
            use_ssl=endpoint.startswith("https://"),
        )
        logger.info(
            "RustFS 已配置: endpoint=%s, bucket=%s, public=%s",
            endpoint, self._bucket, self._public_base_url,
        )

    def is_enabled(self) -> bool:
        return self._enabled and self._client is not None

    async def upload_bytes(
        self, bytes_: bytes, file_name: str, content_type: str, prefix: str = "documents"
    ) -> str:
        if not self.is_enabled() or self._client is None:
            raise HTTPException(503, "RustFS 未启用或未配置，无法上传文件")

        await self.ensure_bucket()

        clean_prefix = re.sub(r"^\/+|\/+$", "", prefix)
        ext = _extension(file_name) or _guess_ext(content_type)
        safe_base = _sanitize_base_name(file_name)
        key = f"{clean_prefix}/{_format_date_path()}/{safe_base}-{uuid.uuid4()}{ext}"

        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=bytes_,
            ContentType=content_type,
            ContentLength=len(bytes_),
        )

        url = f"{self._public_base_url}/{self._bucket}/{key}"
        logger.info("RustFS 上传成功: key=%s, size=%s, url=%s", key, len(bytes_), url)
        return url

    async def ensure_bucket(self) -> None:
        if self._client is None:
            return
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
            return
        except ClientError:
            pass
        try:
            await asyncio.to_thread(self._client.create_bucket, Bucket=self._bucket)
            logger.info("RustFS bucket 已创建: %s", self._bucket)
        except ClientError as err:
            message = str(err)
            if not re.search(
                r"BucketAlreadyOwnedByYou|BucketAlreadyExists|already exists", message
            ):
                raise


def _format_date_path() -> str:
    d = datetime.datetime.now()
    return f"{d.year}/{d.month:02d}/{d.day:02d}"


def _sanitize_base_name(file_name: str) -> str:
    base = re.sub(r"\.[^.]+$", "", file_name) or "file"
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", base)[:64]


def _extension(file_name: str) -> str:
    if "." not in file_name:
        return ""
    return file_name[file_name.rfind("."):]


def _guess_ext(content_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }.get(content_type, "")
