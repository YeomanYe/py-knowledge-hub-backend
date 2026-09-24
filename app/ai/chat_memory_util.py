"""聊天记忆工具：消息文本抽取、DB 行转消息、改写上下文压缩。"""
from __future__ import annotations


def message_text(message: dict) -> str:
    """消息 dict：{role, content} 或带 parts 的 UI 消息。"""
    if message.get("parts"):
        texts = [
            str(p.get("text") or "")
            for p in message["parts"]
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        ]
        return "".join(texts).strip()
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    return str(content).strip()


def is_working_message(message: dict) -> bool:
    return message.get("role") in ("user", "assistant")


def db_rows_to_messages(rows: list) -> list[dict]:
    """AiMessage ORM 行 → [{role, content}]，时间正序，跳过空正文。"""
    out: list[dict] = []
    for row in rows:
        text = (row.content or "").strip()
        if not text:
            continue
        if row.role == "user":
            out.append({"role": "user", "content": text})
        elif row.role == "assistant":
            out.append({"role": "assistant", "content": text})
    return out


def compact_rewrite_context(history: list[dict]) -> str:
    """给检索改写器用：最近几轮，助手只留短摘要，避免制度原文污染 query。"""
    if not history:
        return ""
    lines: list[str] = []
    for message in history[-4:]:
        text = message_text(message)
        if not text:
            continue
        if message.get("role") == "user":
            lines.append(f"用户：{text[:200]}")
        elif message.get("role") == "assistant":
            lines.append(f"助手：{text[:120]}")
    return "\n".join(lines)
