"""Bocha 联网搜索。未配置 BOCHA_API_KEY 时返回提示，不抛错。"""
from __future__ import annotations

import logging

import aiohttp

from ..config import get_settings

logger = logging.getLogger("web-search")


class WebSearchService:
    async def search(self, query: str, count: int = 5) -> dict:
        api_key = get_settings().bocha_api_key
        if not api_key:
            return {
                "query": query,
                "items": [],
                "error": "未配置 BOCHA_API_KEY，无法联网搜索",
            }

        count = max(1, min(count, 10))
        payload = {
            "query": query,
            "freshness": "noLimit",
            "summary": True,
            "count": count,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.bochaai.com/v1/web-search",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    if response.status != 200:
                        detail = (await response.text())[:120]
                        logger.warning("Bocha 搜索失败：status=%s", response.status)
                        return {"query": query, "items": [], "error": f"搜索失败（{response.status}）{detail}"}
                    json_body = await response.json()
        except Exception as err:
            logger.warning("Bocha 搜索异常：%s", err)
            return {"query": query, "items": [], "error": f"搜索失败：{err}"}

        if json_body.get("code") != 200 or not json_body.get("data"):
            return {"query": query, "items": [], "error": json_body.get("msg") or "搜索接口返回异常"}

        items = []
        for page in (json_body.get("data", {}).get("webPages", {}) or {}).get("value", []) or []:
            url = page.get("url")
            name = page.get("name")
            if not url or not name:
                continue
            items.append(
                {
                    "title": str(name),
                    "url": str(url),
                    "snippet": str(page.get("summary") or page.get("snippet") or "").strip(),
                    "siteName": page.get("siteName"),
                }
            )
        return {"query": query, "items": items}
