from __future__ import annotations

import json


def parse_relation_meta(relation_meta: object) -> dict[str, object] | None:
    if not relation_meta:
        return None
    try:
        parsed = json.loads(str(relation_meta))
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def build_evidence_bundle(relation_meta: object) -> dict[str, object]:
    parsed = parse_relation_meta(relation_meta) or {}
    memory_context_items = _normalize_memory_context_items(
        parsed.get("memory_context_items") or parsed.get("related_items")
    )
    citations = _normalize_evidence_citations(parsed.get("evidence_citations"))
    grounded_claims = _normalize_grounded_claims(parsed.get("grounded_claims"), citations=citations)
    summary_segments = _normalize_summary_segments(parsed.get("summary_segments"), citations=citations)
    return {
        "memory_context_items": memory_context_items,
        "citations": citations,
        "grounded_claims": grounded_claims,
        "summary_segments": summary_segments,
        "memory_context_count": len(memory_context_items),
        "evidence_citation_count": len(citations),
        "grounded_claim_count": len(grounded_claims),
    }


def build_summary_relation_meta(
    *,
    memory_context_items: list[dict[str, object]],
    evidence_citations: list[dict[str, object]],
    grounded_claims: list[dict[str, object]],
    summary_segments: list[dict[str, object]],
) -> dict[str, object]:
    normalized_memory_context_items = _normalize_memory_context_items(memory_context_items)
    normalized_citations = _normalize_evidence_citations(evidence_citations)
    normalized_grounded_claims = _normalize_grounded_claims(
        grounded_claims,
        citations=normalized_citations,
    )
    normalized_summary_segments = _normalize_summary_segments(
        summary_segments,
        citations=normalized_citations,
    )
    return {
        "memory_context_items": normalized_memory_context_items,
        "related_items": normalized_memory_context_items,
        "evidence_citations": normalized_citations,
        "grounded_claims": normalized_grounded_claims,
        "summary_segments": normalized_summary_segments,
    }


def _normalize_memory_context_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "snapshot_id": str(item.get("snapshot_id") or ""),
                "knowledge_item_id": str(item.get("knowledge_item_id") or ""),
                "title": title,
                "final_category": str(item.get("final_category") or ""),
                "score": float(item.get("score") or 0.0),
            }
        )
    return items


def _normalize_evidence_citations(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    citations: list[dict[str, object]] = []
    for citation in value:
        if not isinstance(citation, dict):
            continue
        citation_id = str(citation.get("citation_id") or "").strip()
        if not citation_id:
            continue
        citations.append(
            {
                "citation_id": citation_id,
                "rank": int(citation.get("rank") or 0),
                "knowledge_item_id": str(citation.get("knowledge_item_id") or ""),
                "chunk_id": str(citation.get("chunk_id") or ""),
                "parent_chunk_id": str(citation.get("parent_chunk_id") or ""),
                "title": str(citation.get("title") or citation.get("source_name") or ""),
                "section_title": str(citation.get("section_title") or ""),
                "source_type": str(citation.get("source_type") or ""),
                "source_name": str(citation.get("source_name") or ""),
                "source_value": str(citation.get("source_value") or ""),
                "created_at": str(citation.get("created_at") or ""),
                "snippet": str(citation.get("snippet") or ""),
                "context_snippet": str(citation.get("context_snippet") or ""),
                "expanded_context_snippet": str(citation.get("expanded_context_snippet") or ""),
            }
        )
    return citations


def _normalize_grounded_claims(
    value: object,
    *,
    citations: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    title_by_citation_id = {
        str(citation["citation_id"]): str(citation["title"])
        for citation in citations
        if str(citation.get("citation_id") or "").strip()
    }
    valid_citation_ids = set(title_by_citation_id)

    grounded_claims: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        citation_ids_raw = item.get("citation_ids")
        if not claim or not isinstance(citation_ids_raw, list):
            continue
        citation_ids: list[str] = []
        evidence_titles: list[str] = []
        for citation_id in citation_ids_raw:
            normalized_id = str(citation_id).strip()
            if (
                not normalized_id
                or normalized_id in citation_ids
                or normalized_id not in valid_citation_ids
            ):
                continue
            citation_ids.append(normalized_id)
            title = title_by_citation_id.get(normalized_id)
            if title and title not in evidence_titles:
                evidence_titles.append(title)
        if citation_ids:
            grounded_claims.append(
                {
                    "claim": claim,
                    "citation_ids": citation_ids,
                    "evidence_titles": evidence_titles,
                }
            )
    return grounded_claims


def _normalize_summary_segments(
    value: object,
    *,
    citations: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    title_by_citation_id = {
        str(citation["citation_id"]): str(citation["title"])
        for citation in citations
        if str(citation.get("citation_id") or "").strip()
    }
    valid_citation_ids = set(title_by_citation_id)

    summary_segments: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        citation_ids_raw = item.get("citation_ids")
        if not text or not isinstance(citation_ids_raw, list):
            continue
        citation_ids: list[str] = []
        evidence_titles: list[str] = []
        for citation_id in citation_ids_raw:
            normalized_id = str(citation_id).strip()
            if (
                not normalized_id
                or normalized_id in citation_ids
                or normalized_id not in valid_citation_ids
            ):
                continue
            citation_ids.append(normalized_id)
            title = title_by_citation_id.get(normalized_id)
            if title and title not in evidence_titles:
                evidence_titles.append(title)
        if citation_ids:
            summary_segments.append(
                {
                    "text": text,
                    "citation_ids": citation_ids,
                    "evidence_titles": evidence_titles,
                }
            )
    return summary_segments
