"""文件 → Markdown 解析服务。"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from .docx_parser import parse_docx
from .markdown_util import get_extension
from .pdf_parser import parse_pdf
from .plain_text_parser import parse_plain_text
from .pptx_parser import parse_pptx
from .xlsx_parser import parse_xlsx

logger = logging.getLogger("file-parser")

SUPPORTED_EXTENSIONS = {"pdf", "docx", "xlsx", "pptx", "txt", "md"}


class FileParserService:
    def __init__(self, rustfs) -> None:
        self._rustfs = rustfs

    def is_supported(self, extension: str) -> bool:
        return (extension or "").lower() in SUPPORTED_EXTENSIONS

    def supported_list(self) -> str:
        return ", ".join(sorted(SUPPORTED_EXTENSIONS))

    async def parse(self, originalname: str, buffer: bytes, size: int | None = None) -> str:
        extension = get_extension(originalname)

        if not self.is_supported(extension):
            raise HTTPException(
                400,
                f"不支持的文件格式: {extension or '(无扩展名)'}，支持的格式: {self.supported_list()}",
            )

        if not buffer:
            raise HTTPException(400, "文件内容为空，无法解析")

        if extension == "docx":
            result = parse_docx(buffer)
        elif extension == "pdf":
            async def _upload_image(data: bytes, file_name: str, content_type: str) -> str:
                return await self._rustfs.upload_bytes(
                    data,
                    file_name=file_name,
                    content_type=content_type,
                    prefix="pdf-images",
                )

            result = await parse_pdf(
                buffer,
                upload_image=_upload_image if self._rustfs.is_enabled() else None,
            )
        elif extension == "pptx":
            result = parse_pptx(buffer)
        elif extension == "xlsx":
            result = self._parse_xlsx_with_fallback(buffer)
        elif extension in ("txt", "md"):
            result = parse_plain_text(buffer)
        else:
            raise HTTPException(400, f"不支持的文件格式: {extension}")

        logger.info(
            "文件解析完成: name=%s, format=%s, chars=%s", originalname, extension, len(result)
        )

        if not (result or "").strip():
            raise HTTPException(
                400, "文件解析结果为空，请确认文件包含可提取的文本内容"
            )
        return result

    def _parse_xlsx_with_fallback(self, buffer: bytes) -> str:
        try:
            return parse_xlsx(buffer)
        except Exception as err:
            logger.warning("XLSX(openpyxl) 解析失败: %s", err)
            raise HTTPException(400, f"XLSX 解析失败: {err}")
