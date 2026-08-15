from __future__ import annotations

import json
import re
from dataclasses import asdict

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.qa import QAAnswerRequest
from backend.app.services.ai import (
    AnswerArtifact,
    QueryRewriteArtifact,
    create_answer_provider,
    create_query_rewrite_provider,
)
from backend.app.services.evidence_bundle import build_evidence_bundle
from backend.app.services.retrieval import build_retrieval_context
from backend.app.services.retrieval_types import RetrievalFilters, RetrievalQuery
from backend.app.utils import new_id, utc_now

_SESSION_HISTORY_LIMIT = 6
_FOLLOW_UP_MARKERS_ZH = (
    "它",
    "它们",
    "这个",
    "这个点",
    "这点",
    "这些",
    "那",
    "那个",
    "那些",
    "上述",
    "上面",
    "前面",
    "继续",
    "再展开",
    "再说说",
    "详细讲讲",
    "还有呢",
    "然后呢",
)
_FOLLOW_UP_MARKERS_EN = {"their", "them", "they", "those", "this", "that", "these", "it", "its"}
_WORD_TOKEN_RE = re.compile(r"[a-zA-Z]+")
_VERIFY_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*|[\u4e00-\u9fff]{2,}")
_VERIFY_STOPWORDS = {
    "about",
    "after",
    "answer",
    "based",
    "because",
    "before",
    "being",
    "between",
    "could",
    "does",
    "from",
    "have",
    "into",
    "that",
    "their",
    "there",
    "these",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}


def answer_question(
    *,
    db: Database,
    config: AppConfig,
    payload: QAAnswerRequest,
) -> dict[str, object]:
    session_id = payload.session_id or new_id()
    session = _get_qa_session_row(db=db, session_id=session_id)
    if payload.session_id and session is None:
        raise AppError(
            status_code=404,
            error_category="NOT_FOUND",
            error_message="QA session not found.",
        )

    history = _load_session_history(db=db, session_id=session_id, limit=_SESSION_HISTORY_LIMIT)
    rewrite = rewrite_qa_question(
        question=payload.question,
        mode=payload.mode,
        history=history,
    )
    rewrite = _build_structured_query_rewrite(
        config=config,
        question=payload.question,
        mode=payload.mode,
        history=history,
        heuristic_rewrite=rewrite,
    )
    rewritten_question = _read_rewritten_question(rewrite, fallback=payload.question)

    request_filters = payload.filters
    retrieval_filters = RetrievalFilters(
        source_types=request_filters.source_types if request_filters else None,
        created_at_from=request_filters.created_at_from if request_filters else None,
        created_at_to=request_filters.created_at_to if request_filters else None,
        knowledge_item_ids=request_filters.knowledge_item_ids if request_filters else None,
        keyword=request_filters.keyword if request_filters else None,
        category=request_filters.category if request_filters else None,
        user_tags=request_filters.user_tags if request_filters else None,
        ai_tags=request_filters.ai_tags if request_filters else None,
    )
    response_payload = _run_answer_attempt(
        db=db,
        config=config,
        session_id=session_id,
        mode=payload.mode,
        question=payload.question,
        rewritten_question=rewritten_question,
        rewrite_meta=_serialize_rewrite(rewrite, fallback_question=payload.question),
        retrieval_filters=retrieval_filters,
        limit=payload.limit,
        retry_count=0,
    )
    should_retry = (
        response_payload.get("answer_status") == "insufficient_evidence"
        and _response_verification_status(response_payload) == "failed"
        and payload.limit < 10
    )
    if should_retry:
        response_payload = _run_answer_attempt(
            db=db,
            config=config,
            session_id=session_id,
            mode=payload.mode,
            question=payload.question,
            rewritten_question=rewritten_question,
            rewrite_meta=_serialize_rewrite(rewrite, fallback_question=payload.question),
            retrieval_filters=retrieval_filters,
            limit=10,
            retry_count=1,
        )

    _persist_exchange(
        db=db,
        session_id=session_id,
        mode=payload.mode,
        question=payload.question,
        response_payload=response_payload,
    )
    return response_payload


def _run_answer_attempt(
    *,
    db: Database,
    config: AppConfig,
    session_id: str,
    mode: str,
    question: str,
    rewritten_question: str,
    rewrite_meta: dict[str, object],
    retrieval_filters: RetrievalFilters,
    limit: int,
    retry_count: int,
) -> dict[str, object]:
    retrieval_result = build_retrieval_context(
        db=db,
        config=config,
        query=RetrievalQuery(
            text=rewritten_question,
            filters=retrieval_filters,
            limit=limit,
        ),
    )
    citations = [asdict(citation) for citation in retrieval_result.citations]
    used_grounded_items = _load_latest_grounded_items(
        db=db,
        knowledge_item_ids=[citation["knowledge_item_id"] for citation in citations],
    )

    if not citations:
        response_payload = _build_insufficient_evidence_response(
            session_id=session_id,
            mode=mode,
            question=question,
            rewritten_question=rewritten_question,
            rewrite_meta=rewrite_meta,
            filters=retrieval_filters,
            used_grounded_items=[],
            verification=_verification_payload(status="failed", reason="no_citations"),
            retry_count=retry_count,
        )
        return response_payload

    answer_provider = create_answer_provider(config)
    answer_artifact = answer_provider.answer(
        question=rewritten_question,
        mode=mode,
        evidence_citations=citations,
        grounded_items=used_grounded_items,
    )
    valid_citation_ids = {citation["citation_id"] for citation in citations}
    selected_citation_ids = [
        citation_id
        for citation_id in answer_artifact.citation_ids
        if citation_id in valid_citation_ids
    ]
    selected_citations = [
        citation for citation in citations if citation["citation_id"] in selected_citation_ids
    ]

    if answer_artifact.answer_status == "grounded" and not selected_citations:
        response_payload = _build_insufficient_evidence_response(
            session_id=session_id,
            mode=mode,
            question=question,
            rewritten_question=rewritten_question,
            rewrite_meta=rewrite_meta,
            filters=retrieval_filters,
            used_grounded_items=used_grounded_items,
            verification=_verification_payload(
                status="failed",
                reason="missing_valid_citations",
            ),
            retry_count=retry_count,
        )
        return response_payload

    verification = _verify_answer_support(
        answer_artifact=answer_artifact,
        selected_citations=selected_citations,
        grounded_items=used_grounded_items,
    )
    if answer_artifact.answer_status == "grounded" and verification["status"] == "failed":
        return _build_insufficient_evidence_response(
            session_id=session_id,
            mode=mode,
            question=question,
            rewritten_question=rewritten_question,
            rewrite_meta=rewrite_meta,
            filters=retrieval_filters,
            used_grounded_items=used_grounded_items,
            verification=verification,
            retry_count=retry_count,
        )

    suggested_queries = (
        list(answer_artifact.suggested_queries)
        if answer_artifact.suggested_queries
        else _default_suggested_queries(question)
    )
    if answer_artifact.answer_status == "grounded":
        suggested_queries = []

    answer_text = answer_artifact.answer.strip() or "未找到足够依据来回答该问题。"
    if answer_artifact.answer_status == "insufficient_evidence":
        answer_text = "未找到足够依据来回答该问题。"

    response_payload = {
        "session_id": session_id,
        "mode": mode,
        "rewritten_question": rewritten_question,
        "rewrite": rewrite_meta,
        "question": question,
        "answer": answer_text,
        "answer_status": answer_artifact.answer_status,
        "confidence": round(max(0.0, min(1.0, float(answer_artifact.confidence))), 3),
        "applied_filters": _serialize_filters(retrieval_filters),
        "citations": selected_citations,
        "used_grounded_items": used_grounded_items,
        "suggested_queries": suggested_queries[:3],
        "verification": verification,
        "retry_count": retry_count,
    }
    return response_payload


def rewrite_qa_question(
    *,
    question: str,
    mode: str,
    history: list[dict[str, object]],
) -> dict[str, object]:
    normalized_question = question.strip()
    if not history:
        return {
            "rewritten_question": normalized_question,
            "requires_history": False,
            "used_history": False,
            "intent": mode,
            "risk_flags": ["self_contained"],
            "confidence": 0.72,
            "strategy": "heuristic",
            "mode": mode,
        }

    latest_user_question = ""
    latest_assistant_answer = ""
    for item in reversed(history):
        role = str(item.get("role") or "")
        if role == "user" and not latest_user_question:
            latest_user_question = str(item.get("content") or "")
        if role == "assistant" and not latest_assistant_answer:
            latest_assistant_answer = str(item.get("content") or "")
        if latest_user_question and latest_assistant_answer:
            break

    looks_like_follow_up = _looks_like_follow_up_question(normalized_question)
    if not latest_user_question or not looks_like_follow_up:
        return {
            "rewritten_question": normalized_question,
            "requires_history": False,
            "used_history": False,
            "intent": mode,
            "risk_flags": ["self_contained"],
            "confidence": 0.72,
            "strategy": "heuristic",
            "mode": mode,
        }
    rewritten = f"{latest_user_question}\n追问：{normalized_question}"
    if latest_assistant_answer:
        rewritten = f"{rewritten}\n上一轮回答摘要：{latest_assistant_answer[:180]}"
    return {
        "rewritten_question": rewritten.strip(),
        "requires_history": True,
        "used_history": True,
        "intent": "follow_up",
        "risk_flags": ["uses_session_history"],
        "confidence": 0.78,
        "strategy": "heuristic",
        "mode": mode,
    }


def _looks_like_follow_up_question(question: str) -> bool:
    normalized = question.strip().lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in _FOLLOW_UP_MARKERS_ZH):
        return True
    english_tokens = set(_WORD_TOKEN_RE.findall(normalized))
    return any(marker in english_tokens for marker in _FOLLOW_UP_MARKERS_EN)


def _read_rewritten_question(rewrite: object, *, fallback: str) -> str:
    if isinstance(rewrite, dict):
        value = rewrite.get("rewritten_question")
        normalized = str(value).strip() if value is not None else ""
        return normalized or fallback
    value = getattr(rewrite, "rewritten_question", None)
    normalized = str(value).strip() if value is not None else ""
    return normalized or fallback


def _build_structured_query_rewrite(
    *,
    config: AppConfig,
    question: str,
    mode: str,
    history: list[dict[str, object]],
    heuristic_rewrite: object,
) -> QueryRewriteArtifact:
    heuristic_payload = _serialize_rewrite(
        heuristic_rewrite,
        fallback_question=question,
    )
    try:
        provider = create_query_rewrite_provider(config)
        return provider.rewrite(
            question=question,
            mode=mode,
            history=history,
            heuristic_rewrite=heuristic_payload,
        )
    except Exception:
        heuristic_payload["risk_flags"] = [
            *list(heuristic_payload.get("risk_flags") or []),
            "rewrite_provider_failed",
        ]
        heuristic_payload["strategy"] = "fallback"
        return _rewrite_artifact_from_payload(heuristic_payload)


def _serialize_rewrite(
    rewrite: object,
    *,
    fallback_question: str,
) -> dict[str, object]:
    if isinstance(rewrite, QueryRewriteArtifact):
        return {
            "rewritten_question": rewrite.rewritten_question or fallback_question,
            "requires_history": bool(rewrite.requires_history),
            "used_history": bool(rewrite.requires_history),
            "intent": rewrite.intent,
            "risk_flags": list(rewrite.risk_flags),
            "confidence": round(max(0.0, min(1.0, float(rewrite.confidence))), 3),
            "strategy": rewrite.strategy,
        }
    if isinstance(rewrite, dict):
        payload = dict(rewrite)
    else:
        payload = {
            "rewritten_question": getattr(rewrite, "rewritten_question", fallback_question),
            "requires_history": getattr(rewrite, "requires_history", False),
            "used_history": getattr(rewrite, "used_history", False),
            "intent": getattr(rewrite, "intent", "answer"),
            "risk_flags": getattr(rewrite, "risk_flags", []) or [],
            "confidence": getattr(rewrite, "confidence", 0.7),
            "strategy": getattr(rewrite, "strategy", "heuristic"),
        }
    rewritten_question = str(payload.get("rewritten_question") or fallback_question).strip()
    requires_history = bool(payload.get("requires_history") or payload.get("used_history"))
    raw_risk_flags = payload.get("risk_flags") or []
    if isinstance(raw_risk_flags, str):
        raw_risk_flags = [raw_risk_flags]
    risk_flags = [
        str(item).strip()
        for item in list(raw_risk_flags)
        if str(item).strip()
    ]
    if requires_history and "uses_session_history" not in risk_flags:
        risk_flags.append("uses_session_history")
    if not requires_history and "self_contained" not in risk_flags:
        risk_flags.append("self_contained")
    return {
        "rewritten_question": rewritten_question or fallback_question,
        "requires_history": requires_history,
        "used_history": requires_history,
        "intent": str(payload.get("intent") or "answer"),
        "risk_flags": risk_flags[:6],
        "confidence": _normalize_rewrite_confidence(payload.get("confidence")),
        "strategy": str(payload.get("strategy") or "heuristic"),
    }


def _rewrite_artifact_from_payload(payload: dict[str, object]) -> QueryRewriteArtifact:
    raw_risk_flags = payload.get("risk_flags") or []
    if isinstance(raw_risk_flags, str):
        raw_risk_flags = [raw_risk_flags]
    return QueryRewriteArtifact(
        rewritten_question=str(payload.get("rewritten_question") or ""),
        requires_history=bool(payload.get("requires_history") or payload.get("used_history")),
        intent=str(payload.get("intent") or "answer"),
        risk_flags=[str(item).strip() for item in list(raw_risk_flags) if str(item).strip()],
        confidence=_normalize_rewrite_confidence(payload.get("confidence")),
        strategy=str(payload.get("strategy") or "heuristic"),
    )


def _normalize_rewrite_confidence(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.7
    return round(max(0.0, min(1.0, score)), 3)


def _response_verification_status(response_payload: dict[str, object]) -> str:
    verification = response_payload.get("verification")
    if isinstance(verification, dict):
        return str(verification.get("status") or "")
    return ""


def _verify_answer_support(
    *,
    answer_artifact: AnswerArtifact,
    selected_citations: list[dict[str, object]],
    grounded_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if answer_artifact.answer_status != "grounded":
        return _verification_payload(status="skipped", reason="answer_not_grounded")
    if not selected_citations:
        return _verification_payload(status="failed", reason="no_selected_citations")

    answer_terms = _extract_verification_terms(answer_artifact.answer)
    if len(answer_terms) < 3:
        return _verification_payload(
            status="passed",
            reason="answer_too_short_for_term_check",
            answer_terms=len(answer_terms),
        )

    evidence_text = "\n".join(
        [
            *(
                " ".join(
                    str(citation.get(key) or "")
                    for key in (
                        "title",
                        "section_title",
                        "snippet",
                        "context_snippet",
                        "expanded_context_snippet",
                    )
                )
                for citation in selected_citations
            ),
            *(
                " ".join(
                    [
                        str(item.get("title") or ""),
                        str(item.get("claim") or ""),
                        " ".join(str(value) for value in item.get("evidence_titles", [])),
                    ]
                )
                for item in (grounded_items or [])
            ),
        ]
    )
    evidence_terms = _extract_verification_terms(evidence_text)
    supported_terms = len(answer_terms & evidence_terms)
    support_ratio = supported_terms / max(len(answer_terms), 1)
    if supported_terms >= 2 or support_ratio >= 0.28:
        return _verification_payload(
            status="passed",
            reason="citation_terms_support_answer",
            supported_terms=supported_terms,
            answer_terms=len(answer_terms),
        )
    return _verification_payload(
        status="failed",
        reason="citation_terms_do_not_support_answer",
        supported_terms=supported_terms,
        answer_terms=len(answer_terms),
    )


def _extract_verification_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw_term in _VERIFY_TERM_RE.findall(text):
        term = raw_term.strip().lower()
        if not term or term in _VERIFY_STOPWORDS:
            continue
        if term.isascii() and len(term) < 2:
            continue
        if not term.isascii() and len(term) > 8:
            for size in (2, 3, 4):
                for index in range(0, len(term) - size + 1):
                    terms.add(term[index : index + size])
            continue
        terms.add(term)
    return terms


def _verification_payload(
    *,
    status: str,
    reason: str,
    supported_terms: int = 0,
    answer_terms: int = 0,
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "supported_terms": supported_terms,
        "answer_terms": answer_terms,
    }


def list_qa_sessions(*, db: Database) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT
              s.id,
              s.title,
              s.mode,
              s.created_at,
              s.updated_at,
              s.last_question,
              COUNT(m.id) AS message_count
            FROM qa_sessions AS s
            LEFT JOIN qa_messages AS m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC, s.id DESC
            """
        ).fetchall()
    return [
        {
            "session_id": str(row["id"]),
            "title": str(row["title"]),
            "mode": str(row["mode"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "last_question": str(row["last_question"]) if row["last_question"] is not None else None,
            "message_count": int(row["message_count"] or 0),
        }
        for row in rows
    ]


def get_qa_session_detail(*, db: Database, session_id: str) -> dict[str, object]:
    session = _get_qa_session_row(db=db, session_id=session_id)
    if session is None:
        raise AppError(
            status_code=404,
            error_category="NOT_FOUND",
            error_message="QA session not found.",
        )
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT
              id,
              role,
              content,
              question,
              rewritten_question,
              rewrite_meta_json,
              answer_status,
              confidence,
              verification_json,
              retry_count,
              applied_filters_json,
              citations_json,
              used_grounded_items_json,
              suggested_queries_json,
              created_at
            FROM qa_messages
            WHERE session_id = ?
            ORDER BY rowid ASC
            """,
            (session_id,),
        ).fetchall()
    messages = [_row_to_message_payload(row) for row in rows]
    return {
        "session_id": str(session["id"]),
        "title": str(session["title"]),
        "mode": str(session["mode"]),
        "created_at": str(session["created_at"]),
        "updated_at": str(session["updated_at"]),
        "last_question": str(session["last_question"]) if session["last_question"] is not None else None,
        "message_count": len(messages),
        "messages": messages,
    }


def delete_qa_session(*, db: Database, session_id: str) -> bool:
    with db.connect() as connection:
        deleted = connection.execute(
            "DELETE FROM qa_sessions WHERE id = ?",
            (session_id,),
        ).rowcount
    return bool(deleted)


def _build_insufficient_evidence_response(
    *,
    session_id: str,
    mode: str,
    question: str,
    rewritten_question: str,
    rewrite_meta: dict[str, object],
    filters: RetrievalFilters,
    used_grounded_items: list[dict[str, object]],
    verification: dict[str, object] | None = None,
    retry_count: int = 0,
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "mode": mode,
        "rewritten_question": rewritten_question,
        "rewrite": rewrite_meta,
        "question": question,
        "answer": "未找到足够依据来回答该问题。",
        "answer_status": "insufficient_evidence",
        "confidence": 0.18,
        "applied_filters": _serialize_filters(filters),
        "citations": [],
        "used_grounded_items": used_grounded_items,
        "suggested_queries": _default_suggested_queries(question),
        "verification": verification
        or _verification_payload(status="skipped", reason="insufficient_evidence"),
        "retry_count": retry_count,
    }


def _serialize_filters(filters: RetrievalFilters) -> dict[str, object]:
    return {
        "source_types": filters.source_types,
        "created_at_from": filters.created_at_from,
        "created_at_to": filters.created_at_to,
        "knowledge_item_ids": filters.knowledge_item_ids,
        "keyword": filters.keyword,
        "category": filters.category,
        "user_tags": filters.user_tags,
        "ai_tags": filters.ai_tags,
    }


def _default_suggested_queries(question: str) -> list[str]:
    normalized = question.strip().rstrip("？?。.!")
    if not normalized:
        return ["请提供更具体的问题或关键词"]
    return [
        f"{normalized}（限定具体章节或标题）",
        f"{normalized}（限定来源类型或标签）",
    ]


def _load_latest_grounded_items(
    *,
    db: Database,
    knowledge_item_ids: list[str],
) -> list[dict[str, object]]:
    unique_ids: list[str] = []
    for knowledge_item_id in knowledge_item_ids:
        normalized = str(knowledge_item_id).strip()
        if normalized and normalized not in unique_ids:
            unique_ids.append(normalized)
    if not unique_ids:
        return []

    placeholders = ",".join("?" for _ in unique_ids)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
              s.id,
              s.knowledge_item_id,
              s.generated_category,
              s.final_category,
              s.relation_meta,
              s.created_at,
              ki.title
            FROM item_result_snapshots AS s
            JOIN knowledge_items AS ki ON ki.id = s.knowledge_item_id
            WHERE s.knowledge_item_id IN ({placeholders})
            ORDER BY s.knowledge_item_id ASC, s.created_at DESC, s.id DESC
            """,
            unique_ids,
        ).fetchall()

    latest_rows: list[object] = []
    seen_item_ids: set[str] = set()
    for row in rows:
        knowledge_item_id = str(row["knowledge_item_id"])
        if knowledge_item_id in seen_item_ids:
            continue
        seen_item_ids.add(knowledge_item_id)
        latest_rows.append(row)

    grounded_items: list[dict[str, object]] = []
    for row in latest_rows:
        evidence_bundle = build_evidence_bundle(row["relation_meta"])
        for claim in evidence_bundle["grounded_claims"]:
            grounded_items.append(
                {
                    "snapshot_id": str(row["id"]),
                    "title": str(row["title"] or row["id"]),
                    "final_category": str(row["final_category"] or row["generated_category"] or ""),
                    "claim": str(claim["claim"]),
                    "citation_ids": [str(entry) for entry in claim["citation_ids"]],
                    "evidence_titles": [str(entry) for entry in claim["evidence_titles"]],
                }
            )
    return grounded_items[:5]


def _get_qa_session_row(*, db: Database, session_id: str):
    with db.connect() as connection:
        return connection.execute(
            """
            SELECT id, title, mode, created_at, updated_at, last_question
            FROM qa_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()


def _load_session_history(
    *,
    db: Database,
    session_id: str,
    limit: int,
) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT role, content, created_at
            FROM qa_messages
            WHERE session_id = ?
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    history = [
        {
            "role": str(row["role"]),
            "content": str(row["content"]),
            "created_at": str(row["created_at"]),
        }
        for row in reversed(rows)
    ]
    return history


def _persist_exchange(
    *,
    db: Database,
    session_id: str,
    mode: str,
    question: str,
    response_payload: dict[str, object],
) -> None:
    now = utc_now()
    title = question.strip()[:120] or "未命名问答"
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM qa_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO qa_sessions (
                  id, title, mode, created_at, updated_at, last_question
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, title, mode, now, now, question),
            )
        else:
            connection.execute(
                """
                UPDATE qa_sessions
                SET mode = ?, updated_at = ?, last_question = ?
                WHERE id = ?
                """,
                (mode, now, question, session_id),
            )

        connection.execute(
            """
            INSERT INTO qa_messages (
              id, session_id, role, content, created_at
            ) VALUES (?, ?, 'user', ?, ?)
            """,
            (new_id(), session_id, question, now),
        )
        connection.execute(
            """
            INSERT INTO qa_messages (
              id,
              session_id,
              role,
              content,
              question,
              rewritten_question,
              rewrite_meta_json,
              answer_status,
              confidence,
              verification_json,
              retry_count,
              applied_filters_json,
              citations_json,
              used_grounded_items_json,
              suggested_queries_json,
              created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                session_id,
                "assistant",
                str(response_payload.get("answer") or ""),
                str(response_payload.get("question") or ""),
                str(response_payload.get("rewritten_question") or ""),
                json.dumps(response_payload.get("rewrite") or {}, ensure_ascii=False),
                str(response_payload.get("answer_status") or ""),
                float(response_payload.get("confidence") or 0.0),
                json.dumps(response_payload.get("verification") or {}, ensure_ascii=False),
                int(response_payload.get("retry_count") or 0),
                json.dumps(response_payload.get("applied_filters") or {}, ensure_ascii=False),
                json.dumps(response_payload.get("citations") or [], ensure_ascii=False),
                json.dumps(response_payload.get("used_grounded_items") or [], ensure_ascii=False),
                json.dumps(response_payload.get("suggested_queries") or [], ensure_ascii=False),
                now,
            ),
        )


def _row_to_message_payload(row) -> dict[str, object]:
    payload = {
        "message_id": str(row["id"]),
        "role": str(row["role"]),
        "content": str(row["content"]),
        "created_at": str(row["created_at"]),
    }
    if payload["role"] != "assistant":
        return payload
    payload.update(
        {
            "question": str(row["question"]) if row["question"] is not None else None,
            "rewritten_question": str(row["rewritten_question"])
            if row["rewritten_question"] is not None
            else None,
            "rewrite": _message_rewrite_payload(row),
            "answer_status": str(row["answer_status"]) if row["answer_status"] is not None else None,
            "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
            "applied_filters": json.loads(str(row["applied_filters_json"] or "{}")),
            "citations": json.loads(str(row["citations_json"] or "[]")),
            "used_grounded_items": json.loads(str(row["used_grounded_items_json"] or "[]")),
            "suggested_queries": json.loads(str(row["suggested_queries_json"] or "[]")),
            "verification": _message_verification_payload(row),
            "retry_count": int(row["retry_count"] or 0),
        }
    )
    return payload


def _message_rewrite_payload(row) -> dict[str, object]:
    raw_payload = str(row["rewrite_meta_json"] or "{}")
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict) or not parsed.get("rewritten_question"):
        parsed = {
            "rewritten_question": str(row["rewritten_question"] or row["question"] or ""),
            "requires_history": False,
            "used_history": False,
            "intent": "answer",
            "risk_flags": ["legacy_session"],
            "confidence": 0.0,
            "strategy": "legacy",
        }
    return _serialize_rewrite(parsed, fallback_question=str(row["question"] or ""))


def _message_verification_payload(row) -> dict[str, object]:
    raw_payload = str(row["verification_json"] or "{}")
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict) or not parsed.get("status"):
        return _verification_payload(status="skipped", reason="legacy_session")
    return _verification_payload(
        status=str(parsed.get("status") or "skipped"),
        reason=str(parsed.get("reason") or "unknown"),
        supported_terms=int(parsed.get("supported_terms") or 0),
        answer_terms=int(parsed.get("answer_terms") or 0),
    )
