"""无 Agent 的等价检索环：查一次 → 评估 → 不足则改写再查一次。"""
from __future__ import annotations

import logging

logger = logging.getLogger("agentic-retrieve")

EMPTY_GRADE = {"ok": False, "reason": "empty", "text": "未检索到资料"}


async def retrieve_and_grade(
    question: str,
    query: str,
    top_k: int,
    user: dict | None,
    retrieval,
    rewrite,
    retried: bool = False,
    previous_query: str | None = None,
) -> dict:
    """单次检索 + 切题评估，返回 {rawHits,hits,grade,eval,usedQuery}。"""
    used_query = query.strip() or question
    display_query = previous_query or used_query if retried else used_query
    retry_query = used_query if retried else None
    try:
        raw_hits = await retrieval.retrieve(used_query, top_k, user)
        grade = await rewrite.grade_hits(question, raw_hits)
        return {
            "rawHits": raw_hits,
            "hits": raw_hits if grade["ok"] else [],
            "grade": grade,
            "eval": _to_eval(grade, display_query, retried, retry_query),
            "usedQuery": used_query,
        }
    except Exception as err:
        return {
            "rawHits": [],
            "hits": [],
            "grade": {**EMPTY_GRADE, "text": f"检索失败：{err}"},
            "eval": {
                "ok": False,
                "reason": "error",
                "text": "改写后再次检索失败" if retried else "检索失败",
                "retried": retried,
                "query": display_query,
                "retryQuery": retry_query,
            },
            "usedQuery": used_query,
        }


async def retrieve_until_relevant(
    question: str,
    query: str,
    top_k: int,
    user: dict | None,
    retrieval,
    rewrite,
    on_eval=None,
    on_rewrite=None,
) -> dict:
    """查一次 → 评估 → 不足则改写再查一次。返回 {hits, eval, usedQuery}。"""
    first = await retrieve_and_grade(
        question=question,
        query=query,
        top_k=top_k,
        user=user,
        retrieval=retrieval,
        rewrite=rewrite,
    )
    if on_eval:
        await on_eval(first["eval"])
    if first["eval"]["ok"] or first["eval"]["reason"] == "error":
        return {"hits": first["hits"], "eval": first["eval"], "usedQuery": first["usedQuery"]}

    retry_query = await rewrite.rewrite_after_retrieve(
        question, first["usedQuery"], first["grade"], first["rawHits"]
    )
    if not retry_query:
        return {"hits": [], "eval": first["eval"], "usedQuery": first["usedQuery"]}

    if on_rewrite:
        await on_rewrite(retry_query)
    second = await retrieve_and_grade(
        question=question,
        query=retry_query,
        top_k=top_k,
        user=user,
        retrieval=retrieval,
        rewrite=rewrite,
        retried=True,
        previous_query=first["usedQuery"],
    )
    if on_eval:
        await on_eval(second["eval"])
    return {"hits": second["hits"], "eval": second["eval"], "usedQuery": second["usedQuery"]}


def _to_eval(grade: dict, query: str, retried: bool, retry_query: str | None) -> dict:
    if grade["reason"] == "relevant":
        return {
            "ok": True,
            "reason": "retried_ok" if retried else "ok",
            "text": f"已改写再查，{grade['text']}" if retried else grade["text"],
            "retried": retried,
            "query": query,
            "retryQuery": retry_query,
        }
    if grade["reason"] == "empty":
        return {
            "ok": False,
            "reason": "retried_empty" if retried else "empty",
            "text": "改写后仍无结果" if retried else grade["text"],
            "retried": retried,
            "query": query,
            "retryQuery": retry_query,
        }
    return {
        "ok": False,
        "reason": "retried_irrelevant" if retried else "irrelevant",
        "text": f"改写后仍不切题：{grade['text']}" if retried else grade["text"],
        "retried": retried,
        "query": query,
        "retryQuery": retry_query,
    }
