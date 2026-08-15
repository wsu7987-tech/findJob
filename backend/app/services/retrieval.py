from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from backend.app.services.ai import create_embedding_provider
from backend.app.services.chunk_index import ChunkVectorStore
from backend.app.services.lexical import build_fts_query, extract_lexical_terms
from backend.app.services.retrieval_index_versions import resolve_active_chunk_index_version
from backend.app.services.retrieval_store import (
    fetch_adjacent_child_chunk_rows,
    fetch_child_chunk_rows,
    fetch_parent_context_rows,
    search_child_chunk_rows_fts,
)
from backend.app.services.retrieval_types import (
    ChildChunkHit,
    CitationPackItem,
    ParentContext,
    RetrievalFilters,
    RetrievalQuery,
    RetrievalResult,
)

_ANCHOR_TOKEN_RE = re.compile(r"[a-zA-Z0-9-]{2,}|[\u4e00-\u9fff]{4,}")


def build_retrieval_context(*, db, config, query: RetrievalQuery) -> RetrievalResult:
    query_vector = list(query.query_vector or [])
    if not query_vector:
        embedding_provider = create_embedding_provider(config)
        query_vector = embedding_provider.embed_texts([query.text])[0]
    lexical_query_text = query.filters.keyword or query.text
    provider_name = config.embedding_provider or "stub-embedding"
    model_name = config.embedding_model or "stub-embedding-model"
    with db.connect() as connection:
        version_tag = resolve_active_chunk_index_version(
            connection,
            provider_name=provider_name,
            model_name=model_name,
        )
        lexical_rows = search_child_chunk_rows_fts(
            connection,
            fts_query=build_fts_query(lexical_query_text) or "",
            filters=query.filters,
            limit=max(query.limit * 4, query.limit),
        )
    vector_store = ChunkVectorStore(
        config=config,
        provider_name=provider_name,
        model_name=model_name,
        version_tag=version_tag,
    )
    candidate_limit = max(query.limit * 4, query.limit)
    vector_hits = vector_store.search_related(
        query_vector,
        limit=candidate_limit,
        filters=_build_vector_search_filters(query.filters),
    )

    if not vector_hits and not lexical_rows:
        return RetrievalResult(
            query_text=query.text,
            filters=query.filters,
            child_hits=[],
            parent_contexts={},
            citations=[],
        )

    vector_scores = {
        str(hit.get("chunk_id") or hit.get("id") or ""): float(hit.get("score", 0.0) or 0.0)
        for hit in vector_hits
        if str(hit.get("chunk_id") or hit.get("id") or "")
    }
    vector_chunk_ids = [str(hit.get("chunk_id") or hit.get("id") or "") for hit in vector_hits]
    vector_chunk_ids = [chunk_id for chunk_id in vector_chunk_ids if chunk_id]
    lexical_chunk_ids = [str(row["chunk_id"]) for row in lexical_rows if str(row["chunk_id"])]
    lexical_rank_scores = _score_lexical_rows(lexical_rows)
    chunk_ids = _dedupe_preserve_order(
        [
            *vector_chunk_ids,
            *lexical_chunk_ids,
        ]
    )
    with db.connect() as connection:
        rows = fetch_child_chunk_rows(connection, [chunk_id for chunk_id in chunk_ids if chunk_id])

    row_by_id = {str(row["chunk_id"]): row for row in rows}
    vector_support_by_item = _count_support_by_item(vector_chunk_ids, row_by_id)
    lexical_support_by_item = _count_support_by_item(lexical_chunk_ids, row_by_id)
    child_hits: list[ChildChunkHit] = []
    for chunk_id in chunk_ids:
        row = row_by_id.get(chunk_id)
        if row is None:
            continue
        metadata_score, content_score = score_keyword_matches(
            keyword=query.filters.keyword or query.text,
            title=row["title"],
            source_name=row["source_name"],
            source_value=row["source_value"],
            content=row["content"],
        )
        hit = ChildChunkHit(
            chunk_id=chunk_id,
            knowledge_item_id=str(row["knowledge_item_id"]),
            parent_chunk_id=str(row["parent_chunk_id"]),
            section_title=row["section_title"],
            content=str(row["content"]),
            source_type=str(row["source_type"]),
            title=row["title"],
            source_name=str(row["source_name"]),
            source_value=str(row["source_value"]),
            created_at=str(row["created_at"]),
            category=row["capture_category"],
            user_tags=_parse_tag_list(row["user_tags_json"]),
            ai_tags=_parse_tag_list(row["ai_tags_json"]),
            vector_score=vector_scores.get(chunk_id, 0.0),
            metadata_keyword_score=metadata_score,
            content_keyword_score=content_score,
            final_score=_final_score(
                vector_score=vector_scores.get(chunk_id, 0.0),
                lexical_score=lexical_rank_scores.get(chunk_id, 0.0),
                metadata_keyword_score=metadata_score,
                content_keyword_score=content_score,
            ),
            lexical_score=lexical_rank_scores.get(chunk_id, 0.0),
        )
        if _matches_filters(hit, query.filters):
            child_hits.append(hit)

    child_hits = _rerank_child_hits(
        child_hits,
        query_text=query.filters.keyword or query.text,
        lexical_support_by_item=lexical_support_by_item,
        vector_support_by_item=vector_support_by_item,
    )
    child_hits = _collapse_hits_by_knowledge_item(child_hits)
    child_hits = _prune_child_hits(
        child_hits,
        limit=query.limit,
        query_text=query.filters.keyword or query.text,
    )
    parent_ids = sorted({item.parent_chunk_id for item in child_hits})

    with db.connect() as connection:
        parent_rows = fetch_parent_context_rows(connection, parent_ids)
        parent_contexts = {
            str(row["parent_chunk_id"]): ParentContext(
                parent_chunk_id=str(row["parent_chunk_id"]),
                knowledge_item_id=str(row["knowledge_item_id"]),
                section_title=row["section_title"],
                content=str(row["content"]),
                title=row["title"],
                source_type=str(row["source_type"]),
                source_name=str(row["source_name"]),
                source_value=str(row["source_value"]),
                created_at=str(row["created_at"]),
                category=row["capture_category"],
                user_tags=_parse_tag_list(row["user_tags_json"]),
                ai_tags=_parse_tag_list(row["ai_tags_json"]),
            )
            for row in parent_rows
        }
        citations = _build_citation_pack(
            connection=connection,
            child_hits=child_hits,
            parent_contexts=parent_contexts,
            row_by_id=row_by_id,
        )
    return RetrievalResult(
        query_text=query.text,
        filters=query.filters,
        child_hits=child_hits,
        parent_contexts=parent_contexts,
        citations=citations,
    )


def score_keyword_matches(
    *,
    keyword: str | None,
    title: str | None,
    source_name: str | None,
    source_value: str | None,
    content: str,
) -> tuple[float, float]:
    terms = extract_lexical_terms(keyword or "")
    if not terms:
        return 0.0, 0.0

    metadata_terms = set(
        extract_lexical_terms(" ".join(value for value in [title or "", source_name or "", source_value or ""] if value))
    )
    content_terms = set(extract_lexical_terms(content))

    metadata_matches = [term for term in terms if term in metadata_terms]
    content_matches = [term for term in terms if term in content_terms]
    metadata_score = _term_match_score(metadata_matches, total_terms=len(terms))
    content_score = 0.5 * _term_match_score(content_matches, total_terms=len(terms))
    return metadata_score, content_score


def _final_score(
    *,
    vector_score: float,
    lexical_score: float,
    metadata_keyword_score: float,
    content_keyword_score: float,
) -> float:
    return (
        0.45 * vector_score
        + 0.25 * lexical_score
        + 0.20 * metadata_keyword_score
        + 0.10 * content_keyword_score
    )


def _matches_filters(hit: ChildChunkHit, filters: RetrievalFilters) -> bool:
    if filters.source_types and hit.source_type not in filters.source_types:
        return False
    if filters.knowledge_item_ids and hit.knowledge_item_id not in filters.knowledge_item_ids:
        return False
    if filters.created_at_from and not _timestamp_on_or_after(hit.created_at, filters.created_at_from):
        return False
    if filters.created_at_to and not _timestamp_on_or_before(hit.created_at, filters.created_at_to):
        return False
    if filters.category and hit.category != filters.category:
        return False
    if filters.user_tags and not any(tag in hit.user_tags for tag in filters.user_tags):
        return False
    if filters.ai_tags and not any(tag in hit.ai_tags for tag in filters.ai_tags):
        return False
    if filters.keyword:
        metadata_score, content_score = score_keyword_matches(
            keyword=filters.keyword,
            title=hit.title,
            source_name=hit.source_name,
            source_value=hit.source_value,
            content=hit.content,
        )
        if metadata_score <= 0 and content_score <= 0:
            return False
    return True


def _parse_tag_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _build_vector_search_filters(filters: RetrievalFilters) -> dict[str, object]:
    payload_filters: dict[str, object] = {}
    if filters.source_types:
        payload_filters["source_types"] = list(filters.source_types)
    if filters.created_at_from:
        payload_filters["created_at_from"] = filters.created_at_from
    if filters.created_at_to:
        payload_filters["created_at_to"] = filters.created_at_to
    if filters.knowledge_item_ids:
        payload_filters["knowledge_item_ids"] = list(filters.knowledge_item_ids)
    if filters.category:
        payload_filters["category"] = filters.category
    if filters.user_tags:
        payload_filters["user_tags"] = list(filters.user_tags)
    if filters.ai_tags:
        payload_filters["ai_tags"] = list(filters.ai_tags)
    return payload_filters


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_on_or_after(value: str | None, boundary: str | None) -> bool:
    parsed_value = _parse_timestamp(value)
    parsed_boundary = _parse_timestamp(boundary)
    if parsed_value is None or parsed_boundary is None:
        return (value or "") >= (boundary or "")
    return parsed_value >= parsed_boundary


def _timestamp_on_or_before(value: str | None, boundary: str | None) -> bool:
    parsed_value = _parse_timestamp(value)
    parsed_boundary = _parse_timestamp(boundary)
    if parsed_value is None or parsed_boundary is None:
        return (value or "") <= (boundary or "")
    return parsed_value <= parsed_boundary


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _rank_scores(values: list[str]) -> dict[str, float]:
    ordered = _dedupe_preserve_order(values)
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0]: 1.0}
    total = len(ordered)
    return {
        value: round(1.0 - (index / total), 6)
        for index, value in enumerate(ordered)
    }


def _score_lexical_rows(rows: list[object]) -> dict[str, float]:
    ordered_chunk_ids = [str(row["chunk_id"]) for row in rows if str(row["chunk_id"])]
    rank_scores = _rank_scores(ordered_chunk_ids)
    if not ordered_chunk_ids:
        return {}

    strengths = {
        str(row["chunk_id"]): abs(float(row["lexical_score"]))
        for row in rows
        if str(row["chunk_id"])
    }
    max_strength = max(strengths.values(), default=0.0)
    min_strength = min(strengths.values(), default=0.0)
    strength_range = max_strength - min_strength
    if strength_range <= 0:
        return rank_scores

    combined_scores: dict[str, float] = {}
    for chunk_id in ordered_chunk_ids:
        normalized_strength = (strengths[chunk_id] - min_strength) / strength_range
        combined_scores[chunk_id] = round((0.65 * normalized_strength) + (0.35 * rank_scores.get(chunk_id, 0.0)), 6)
    return combined_scores


def _term_match_score(matches: list[str], *, total_terms: int) -> float:
    if not matches or total_terms <= 0:
        return 0.0
    coverage_score = len(set(matches)) / min(total_terms, 4)
    longest_term = max(len(term) for term in matches)
    longest_score = min(longest_term / 6, 1.0)
    return min(max(coverage_score, longest_score), 1.0)


def _rerank_child_hits(
    child_hits: list[ChildChunkHit],
    *,
    query_text: str,
    lexical_support_by_item: dict[str, int],
    vector_support_by_item: dict[str, int],
) -> list[ChildChunkHit]:
    if not child_hits:
        return []

    query_terms = extract_lexical_terms(query_text)
    reranked: list[ChildChunkHit] = []
    for hit in child_hits:
        hit.final_score = round(
            hit.final_score
            + _field_match_bonus(query_text, query_terms=query_terms, field_value=hit.title, max_bonus=0.16)
            + _field_match_bonus(query_text, query_terms=query_terms, field_value=hit.section_title, max_bonus=0.22)
            + _anchor_term_adjustment(
                query_text,
                title=hit.title,
                section_title=hit.section_title,
                content=hit.content,
            )
            + _support_bonus(
                lexical_support=lexical_support_by_item.get(hit.knowledge_item_id, 0),
                vector_support=vector_support_by_item.get(hit.knowledge_item_id, 0),
            ),
            6,
        )
        reranked.append(hit)

    reranked.sort(key=lambda item: item.final_score, reverse=True)
    return reranked


def _collapse_hits_by_knowledge_item(child_hits: list[ChildChunkHit]) -> list[ChildChunkHit]:
    collapsed: list[ChildChunkHit] = []
    seen_knowledge_item_ids: set[str] = set()
    for hit in child_hits:
        if hit.knowledge_item_id in seen_knowledge_item_ids:
            continue
        seen_knowledge_item_ids.add(hit.knowledge_item_id)
        collapsed.append(hit)
    return collapsed


def _prune_child_hits(
    child_hits: list[ChildChunkHit],
    *,
    limit: int,
    query_text: str | None = None,
) -> list[ChildChunkHit]:
    if not child_hits:
        return []
    best_score = child_hits[0].final_score
    score_floor = max(best_score * 0.82, best_score - 0.18)
    if query_text:
        top_alignment = _query_alignment_score(child_hits[0], query_text)
        if top_alignment >= 0.2:
            score_floor = max(score_floor, best_score - 0.03)
        if child_hits[0].lexical_score >= 0.85:
            score_floor = max(score_floor, best_score - 0.03)
    pruned = [
        hit
        for index, hit in enumerate(child_hits)
        if index == 0
        or (
            hit.final_score >= score_floor
            and (
                not query_text
                or _query_alignment_score(hit, query_text) + 0.08 >= _query_alignment_score(child_hits[0], query_text)
                or hit.lexical_score + 0.45 >= child_hits[0].lexical_score
                or hit.final_score + 0.02 >= best_score
            )
        )
    ]
    return pruned[:limit]


def _count_support_by_item(chunk_ids: list[str], row_by_id: dict[str, object]) -> dict[str, int]:
    support: dict[str, int] = {}
    for chunk_id in chunk_ids:
        row = row_by_id.get(chunk_id)
        if row is None:
            continue
        knowledge_item_id = str(row["knowledge_item_id"])
        support[knowledge_item_id] = support.get(knowledge_item_id, 0) + 1
    return support


def _field_match_bonus(
    query_text: str,
    *,
    query_terms: list[str],
    field_value: str | None,
    max_bonus: float,
) -> float:
    if not field_value or not query_terms:
        return 0.0

    normalized_query = "".join(query_text.lower().split())
    normalized_field = "".join(field_value.lower().split())
    if normalized_query and normalized_query in normalized_field:
        return max_bonus

    field_terms = set(extract_lexical_terms(field_value))
    matched_terms = [term for term in query_terms if term in field_terms]
    if not matched_terms:
        return 0.0
    return round(max_bonus * _term_match_score(matched_terms, total_terms=len(query_terms)), 6)


def _support_bonus(*, lexical_support: int, vector_support: int) -> float:
    lexical_bonus = min(max(lexical_support - 1, 0) * 0.035, 0.105)
    vector_bonus = min(max(vector_support - 1, 0) * 0.02, 0.06)
    cross_bonus = 0.03 if lexical_support > 0 and vector_support > 0 else 0.0
    return round(min(lexical_bonus + vector_bonus + cross_bonus, 0.15), 6)


def _anchor_term_adjustment(
    query_text: str,
    *,
    title: str | None,
    section_title: str | None,
    content: str,
) -> float:
    anchor_terms = _extract_anchor_terms(query_text)
    if not anchor_terms:
        return 0.0

    header_text = " ".join(part for part in [title or "", section_title or ""] if part).lower()
    content_text = content.lower()
    matched_header_terms = [term for term in anchor_terms if term in header_text]
    matched_content_terms = [term for term in anchor_terms if term in content_text]
    if matched_header_terms:
        longest_term = max(len(term) for term in matched_header_terms)
        density_bonus = min(len(set(matched_header_terms)) * 0.03, 0.12)
        precision_bonus = min(longest_term / 24, 0.16)
        return round(min(density_bonus + precision_bonus, 0.28), 6)
    if matched_content_terms:
        return 0.03

    contains_ascii_anchor = any(any("a" <= char <= "z" or "0" <= char <= "9" for char in term) for term in anchor_terms)
    return -0.12 if contains_ascii_anchor else -0.04


def _extract_anchor_terms(text: str) -> list[str]:
    if not text:
        return []

    anchor_terms: list[str] = []
    for token in _ANCHOR_TOKEN_RE.findall(text.lower()):
        if "-" in token:
            anchor_terms.append(token)
            anchor_terms.extend(part for part in token.split("-") if len(part) >= 2)
            continue
        if re.search(r"[\u4e00-\u9fff]", token):
            anchor_terms.extend(term for term in extract_lexical_terms(token) if len(term) >= 4)
            continue
        anchor_terms.append(token)
    return list(dict.fromkeys(anchor_terms))


def _query_alignment_score(hit: ChildChunkHit, query_text: str) -> float:
    query_terms = extract_lexical_terms(query_text)
    return round(
        _field_match_bonus(query_text, query_terms=query_terms, field_value=hit.title, max_bonus=0.16)
        + _field_match_bonus(query_text, query_terms=query_terms, field_value=hit.section_title, max_bonus=0.22)
        + max(
            _anchor_term_adjustment(
                query_text,
                title=hit.title,
                section_title=hit.section_title,
                content=hit.content,
            ),
            0.0,
        ),
        6,
    )


def _build_citation_pack(
    *,
    connection,
    child_hits: list[ChildChunkHit],
    parent_contexts: dict[str, ParentContext],
    row_by_id: dict[str, object],
) -> list[CitationPackItem]:
    citations: list[CitationPackItem] = []
    for index, hit in enumerate(child_hits, start=1):
        parent_context = parent_contexts.get(hit.parent_chunk_id)
        row = row_by_id.get(hit.chunk_id)
        center_position = int(row["position"]) if row is not None else 0
        adjacent_rows = fetch_adjacent_child_chunk_rows(
            connection,
            parent_chunk_id=hit.parent_chunk_id,
            center_position=center_position,
            window_size=1,
        )
        expanded_context_snippet = "\n\n".join(str(adjacent_row["content"]) for adjacent_row in adjacent_rows)
        citations.append(
            CitationPackItem(
                citation_id=f"cite-{index}",
                rank=index,
                knowledge_item_id=hit.knowledge_item_id,
                chunk_id=hit.chunk_id,
                parent_chunk_id=hit.parent_chunk_id,
                title=hit.title,
                section_title=hit.section_title,
                source_type=hit.source_type,
                source_name=hit.source_name,
                source_value=hit.source_value,
                created_at=hit.created_at,
                snippet=hit.content,
                context_snippet=parent_context.content if parent_context is not None else hit.content,
                expanded_context_snippet=expanded_context_snippet or hit.content,
            )
        )
    return citations
