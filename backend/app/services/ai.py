from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from backend.app.config import AppConfig
from backend.app.errors import AppError


_TOKEN_RE = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]{2,}")
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "been",
    "from",
    "have",
    "into",
    "that",
    "the",
    "their",
    "them",
    "this",
    "with",
    "you",
}
_LLM_PROVIDER_ALIASES = {
    "deepseek": "openai-compatible",
}
_EMBEDDING_PROVIDER_ALIASES = {
    "qianwen": "openai-compatible",
    "qwen": "openai-compatible",
    "dashscope": "openai-compatible",
    "aliyun": "openai-compatible",
}
_HTTP_CLIENT_CACHE: dict[tuple[str, str, int], httpx.Client] = {}
_HTTP_CLIENT_CACHE_LOCK = threading.Lock()
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_BACKOFF_SECONDS = 0.5
_LIGHTWEIGHT_SUMMARY_PROMPT_CHAR_THRESHOLD = 500
_SUMMARY_TEXT_MAX_LENGTH = 200
_TAKEAWAY_MAX_LENGTH = 70
_READING_FOCUS_MAX_ITEMS = 5
_READING_FOCUS_MAX_LENGTH = 40
_KEY_POINTS_MAX_ITEMS = 8
_KEY_POINT_MAX_LENGTH = 60
_METHODS_MAX_ITEMS = 5
_METHOD_STEP_MAX_LENGTH = 30
_PITFALLS_MAX_ITEMS = 3
_PITFALL_MAX_LENGTH = 30
_KEYWORDS_MAX_ITEMS = 8
_CODE_EXAMPLES_MAX_ITEMS = 2
_CODE_SNIPPET_MAX_LENGTH = 120


@dataclass(slots=True)
class RelatedContextItem:
    snapshot_id: str
    knowledge_item_id: str | None
    title: str
    final_category: str | None
    summary_text: str
    score: float


@dataclass(slots=True)
class SummaryArtifact:
    generated_category: str
    generated_tags: list[str]
    one_sentence_takeaway: str | None
    summary_text: str
    viewpoint_text: str | None
    controversy_text: str | None
    reading_focus: list[str]
    key_points: list[str]
    keywords: list[dict[str, object]]
    methods_or_process: list[str]
    pitfalls_or_limits: list[str]
    code_examples: list[dict[str, object]]
    content_quality_score: float
    grounded_claims: list[dict[str, object]]
    summary_segments: list[dict[str, object]]
    quality_meta: dict[str, object]


@dataclass(slots=True)
class AnswerArtifact:
    answer: str
    answer_status: str
    confidence: float
    citation_ids: list[str]
    suggested_queries: list[str]
    quality_meta: dict[str, object]


@dataclass(slots=True)
class QueryRewriteArtifact:
    rewritten_question: str
    requires_history: bool
    intent: str
    risk_flags: list[str]
    confidence: float
    strategy: str


@dataclass(slots=True)
class MetadataSuggestion:
    category: str
    tags: list[str]
    strategy: str


@dataclass(slots=True)
class ProviderConnectivityCheckResult:
    capability: str
    ok: bool
    status: str
    provider: str | None
    model: str | None
    base_url: str | None
    detail: str
    error_category: str | None = None


class SummaryProvider(Protocol):
    def summarize(
        self,
        *,
        title: str,
        source_type: str,
        source_value: str,
        cleaning_level: str | None,
        raw_content: str,
        related_items: list[RelatedContextItem],
        evidence_citations: list[dict[str, object]],
    ) -> SummaryArtifact: ...


class AnswerProvider(Protocol):
    def answer(
        self,
        *,
        question: str,
        mode: str,
        evidence_citations: list[dict[str, object]],
        grounded_items: list[dict[str, object]],
    ) -> AnswerArtifact: ...


class QueryRewriteProvider(Protocol):
    def rewrite(
        self,
        *,
        question: str,
        mode: str,
        history: list[dict[str, object]],
        heuristic_rewrite: dict[str, object],
    ) -> QueryRewriteArtifact: ...


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def suggest_metadata(
    *,
    title: str | None,
    raw_content: str | None,
    source_type: str,
    source_value: str | None = None,
) -> MetadataSuggestion:
    normalized_title = _normalized_string(title) or ""
    normalized_content = _normalized_string(raw_content) or ""
    normalized_source_value = _normalized_string(source_value) or ""
    combined_content = "\n".join(
        part for part in (normalized_title, normalized_content, normalized_source_value) if part
    )
    category = _classify_text(combined_content, source_type)
    tags = _extract_tags(
        title=normalized_title,
        raw_content=normalized_content or normalized_source_value or normalized_title,
        source_type=source_type,
    )
    return MetadataSuggestion(category=category, tags=tags, strategy="heuristic")


def check_llm_connection(config: AppConfig) -> ProviderConnectivityCheckResult:
    provider = _normalized_string(config.llm_provider)
    model = _normalized_string(config.llm_model)
    missing = _missing_connectivity_fields(config, capability="llm")
    if missing:
        return ProviderConnectivityCheckResult(
            capability="llm",
            ok=False,
            status="invalid",
            provider=provider,
            model=model,
            base_url=_normalized_string(config.llm_base_url),
            detail=f"Missing required config: {', '.join(missing)}",
            error_category="CONFIG_INVALID",
        )

    normalized_provider = _normalize_llm_provider(provider)
    if normalized_provider == "stub-llm":
        return ProviderConnectivityCheckResult(
            capability="llm",
            ok=True,
            status="ready",
            provider=provider,
            model=model,
            base_url=None,
            detail="Stub LLM provider is ready.",
        )

    base_url = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    try:
        _probe_llm_connection(
            base_url=base_url,
            api_key=config.llm_api_key or "",
            model_name=model or "",
            timeout_seconds=config.llm_timeout_seconds,
        )
    except AppError as exc:
        return ProviderConnectivityCheckResult(
            capability="llm",
            ok=False,
            status="failed",
            provider=provider,
            model=model,
            base_url=base_url,
            detail=exc.error_message,
            error_category=exc.error_category,
        )

    return ProviderConnectivityCheckResult(
        capability="llm",
        ok=True,
        status="ready",
        provider=provider,
        model=model,
        base_url=base_url,
        detail="LLM connectivity check passed.",
    )


def check_embedding_connection(config: AppConfig) -> ProviderConnectivityCheckResult:
    provider = _normalized_string(config.embedding_provider)
    model = _normalized_string(config.embedding_model)
    base_url = _normalized_string(config.embedding_base_url or config.llm_base_url)
    missing = _missing_connectivity_fields(config, capability="embedding")
    if missing:
        return ProviderConnectivityCheckResult(
            capability="embedding",
            ok=False,
            status="invalid",
            provider=provider,
            model=model,
            base_url=base_url,
            detail=f"Missing required config: {', '.join(missing)}",
            error_category="CONFIG_INVALID",
        )

    normalized_provider = _normalize_embedding_provider(provider)
    if normalized_provider == "stub-embedding":
        return ProviderConnectivityCheckResult(
            capability="embedding",
            ok=True,
            status="ready",
            provider=provider,
            model=model,
            base_url=None,
            detail="Stub embedding provider is ready.",
        )

    resolved_base_url = (config.embedding_base_url or config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    try:
        _probe_embedding_connection(
            base_url=resolved_base_url,
            api_key=config.embedding_api_key or config.llm_api_key or "",
            model_name=model or "",
            timeout_seconds=config.embedding_timeout_seconds,
        )
    except AppError as exc:
        return ProviderConnectivityCheckResult(
            capability="embedding",
            ok=False,
            status="failed",
            provider=provider,
            model=model,
            base_url=resolved_base_url,
            detail=exc.error_message,
            error_category=exc.error_category,
        )

    return ProviderConnectivityCheckResult(
        capability="embedding",
        ok=True,
        status="ready",
        provider=provider,
        model=model,
        base_url=resolved_base_url,
        detail="Embedding connectivity check passed.",
    )


def validate_provider_config(config: AppConfig) -> list[str]:
    missing: list[str] = []

    llm_provider = _normalize_llm_provider(config.llm_provider)
    if llm_provider in {"openai", "openai-compatible"}:
        if llm_provider == "openai-compatible" and not config.llm_base_url:
            missing.append("llm_base_url")
        if not config.llm_api_key:
            missing.append("llm_api_key")

    embedding_provider = _normalize_embedding_provider(config.embedding_provider)
    if embedding_provider in {"openai", "openai-compatible"}:
        if (
            embedding_provider == "openai-compatible"
            and not (config.embedding_base_url or config.llm_base_url)
        ):
            missing.append("embedding_base_url")
        if not (config.embedding_api_key or config.llm_api_key):
            missing.append("embedding_api_key")

    return missing


def create_summary_provider(config: AppConfig) -> SummaryProvider:
    provider = _normalize_llm_provider(config.llm_provider or "stub-llm")
    if provider == "stub-llm":
        return StubSummaryProvider(model_name=config.llm_model or "stub-summary-model")
    if provider in {"openai", "openai-compatible"}:
        base_url = config.llm_base_url or "https://api.openai.com/v1"
        if not config.llm_api_key:
            raise AppError(
                status_code=400,
                error_category="CONFIG_INVALID",
                error_message="KNOWLEDGE_CURATOR_LLM_API_KEY is required for the selected llm_provider.",
            )
        return OpenAICompatibleSummaryProvider(
            base_url=base_url.rstrip("/"),
            api_key=config.llm_api_key,
            model_name=config.llm_model or "",
            timeout_seconds=config.llm_timeout_seconds,
        )
    raise AppError(
        status_code=400,
        error_category="CONFIG_INVALID",
        error_message=f"Unsupported llm_provider: {config.llm_provider}",
    )


def create_answer_provider(config: AppConfig) -> AnswerProvider:
    provider = _normalize_llm_provider(config.llm_provider or "stub-llm")
    if provider == "stub-llm":
        return StubAnswerProvider(model_name=config.llm_model or "stub-answer-model")
    if provider in {"openai", "openai-compatible"}:
        base_url = config.llm_base_url or "https://api.openai.com/v1"
        if not config.llm_api_key:
            raise AppError(
                status_code=400,
                error_category="CONFIG_INVALID",
                error_message="KNOWLEDGE_CURATOR_LLM_API_KEY is required for the selected llm_provider.",
            )
        return OpenAICompatibleAnswerProvider(
            base_url=base_url.rstrip("/"),
            api_key=config.llm_api_key,
            model_name=config.llm_model or "",
            timeout_seconds=config.llm_timeout_seconds,
        )
    raise AppError(
        status_code=400,
        error_category="CONFIG_INVALID",
        error_message=f"Unsupported llm_provider: {config.llm_provider}",
    )


def create_query_rewrite_provider(config: AppConfig) -> QueryRewriteProvider:
    provider = _normalize_llm_provider(config.llm_provider or "stub-llm")
    if provider == "stub-llm":
        return StubQueryRewriteProvider(model_name=config.llm_model or "stub-rewrite-model")
    if provider in {"openai", "openai-compatible"}:
        base_url = config.llm_base_url or "https://api.openai.com/v1"
        if not config.llm_api_key:
            raise AppError(
                status_code=400,
                error_category="CONFIG_INVALID",
                error_message="KNOWLEDGE_CURATOR_LLM_API_KEY is required for the selected llm_provider.",
            )
        return OpenAICompatibleQueryRewriteProvider(
            base_url=base_url.rstrip("/"),
            api_key=config.llm_api_key,
            model_name=config.llm_model or "",
            timeout_seconds=config.llm_timeout_seconds,
        )
    raise AppError(
        status_code=400,
        error_category="CONFIG_INVALID",
        error_message=f"Unsupported llm_provider: {config.llm_provider}",
    )


def create_embedding_provider(config: AppConfig) -> EmbeddingProvider:
    provider = _normalize_embedding_provider(config.embedding_provider or "stub-embedding")
    if provider == "stub-embedding":
        return StubEmbeddingProvider(model_name=config.embedding_model or "stub-embedding-model")
    if provider in {"openai", "openai-compatible"}:
        base_url = config.embedding_base_url or config.llm_base_url or "https://api.openai.com/v1"
        api_key = config.embedding_api_key or config.llm_api_key
        if not api_key:
            raise AppError(
                status_code=400,
                error_category="CONFIG_INVALID",
                error_message="An embedding API key is required for the selected embedding_provider.",
            )
        return OpenAICompatibleEmbeddingProvider(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model_name=config.embedding_model or "",
            timeout_seconds=config.embedding_timeout_seconds,
        )
    raise AppError(
        status_code=400,
        error_category="CONFIG_INVALID",
        error_message=f"Unsupported embedding_provider: {config.embedding_provider}",
    )


class StubSummaryProvider:
    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name

    def summarize(
        self,
        *,
        title: str,
        source_type: str,
        source_value: str,
        cleaning_level: str | None,
        raw_content: str,
        related_items: list[RelatedContextItem],
        evidence_citations: list[dict[str, object]],
    ) -> SummaryArtifact:
        summary_text = raw_content
        viewpoint_text = title
        controversy_text = "无"
        generated_tags = _extract_tags(title=title, raw_content=raw_content, source_type=source_type)
        generated_category = "general"
        score = min(0.99, max(0.2, len(raw_content) / 4000))
        grounded_claims = _normalize_grounded_claims(
            [
                {
                    "claim": summary_text[:280],
                    "citation_ids": [
                        str(evidence_citations[0].get("citation_id") or "").strip()
                    ],
                }
            ]
            if evidence_citations
            else []
        )
        return SummaryArtifact(
            generated_category=generated_category,
            generated_tags=generated_tags,
            summary_text=summary_text,
            viewpoint_text=viewpoint_text,
            controversy_text=controversy_text,
            content_quality_score=round(score, 3),
            grounded_claims=grounded_claims,
            summary_segments=_normalize_summary_segments(
                [
                    {
                        "text": summary_text[:280],
                        "citation_ids": [
                            str(evidence_citations[0].get("citation_id") or "").strip()
                        ],
                    }
                ]
                if evidence_citations
                else [],
                fallback_grounded_claims=grounded_claims,
            ),
            quality_meta={
                "provider": "stub-llm",
                "model": self.model_name,
                "source_value": source_value,
                "cleaning_level": cleaning_level or "unknown",
                "memory_context_count": len(related_items),
                "evidence_citation_count": len(evidence_citations),
                "related_context_count": len(related_items),
                "input_characters": len(raw_content),
            },
        )


class StubEmbeddingProvider:
    def __init__(self, *, model_name: str, dimensions: int = 256) -> None:
        self.model_name = model_name
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in _tokenize(text):
                bucket = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dimensions
                vector[bucket] += 1.0
            vectors.append(_normalize_vector(vector))
        return vectors


class OpenAICompatibleSummaryProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def summarize(
        self,
        *,
        title: str,
        source_type: str,
        source_value: str,
        cleaning_level: str | None,
        raw_content: str,
        related_items: list[RelatedContextItem],
        evidence_citations: list[dict[str, object]],
    ) -> SummaryArtifact:
        memory_context = "\n".join(
            (
                f"- title: {item.title}\n"
                f"  category: {item.final_category or 'unknown'}\n"
                f"  score: {item.score:.3f}\n"
                f"  summary: {item.summary_text[:500]}"
            )
            for item in related_items[:5]
        ) or "- none"
        evidence_context = "\n".join(
            (
                f"- citation_id: {str(citation.get('citation_id') or '')}\n"
                f"  title: {str(citation.get('title') or citation.get('source_name') or '')}\n"
                f"  section: {str(citation.get('section_title') or '')}\n"
                f"  snippet: {str(citation.get('snippet') or '')[:700]}\n"
                f"  context: {str(citation.get('expanded_context_snippet') or citation.get('context_snippet') or '')[:900]}"
            )
            for citation in evidence_citations[:5]
        ) or "- none"
        prompt = (
            "You are summarizing a knowledge item for a personal research archive.\n"
            "Return strict JSON with keys: generated_category, generated_tags, summary_text, "
            "viewpoint_text, controversy_text, content_quality_score, grounded_claims, summary_segments.\n"
            "generated_tags must be an array of short strings. content_quality_score must be a number between 0 and 1.\n"
            "grounded_claims must be an array of objects with keys claim and citation_ids.\n"
            "summary_segments must be an array of objects with keys text and citation_ids.\n"
            "Prefer evidence_context as the primary factual basis. Use memory_context only as secondary background.\n"
            "If the source content is primarily Chinese, write all natural-language fields in Simplified Chinese.\n"
            "Otherwise, keep the dominant language of the source content.\n"
            f"title: {title}\n"
            f"source_type: {source_type}\n"
            f"source_value: {source_value}\n"
            f"cleaning_level: {cleaning_level or 'unknown'}\n"
            f"memory_context:\n{memory_context}\n\n"
            f"evidence_context:\n{evidence_context}\n\n"
            f"content:\n{raw_content[:12000]}"
        )
        payload = {
            "model": self.model_name,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Produce concise, grounded summaries. Do not invent facts.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        response_payload = _post_json(
            url=f"{self.base_url}/chat/completions",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            payload=payload,
        )
        content = _extract_chat_completion_content(response_payload)
        parsed = _parse_json_object(content, context="llm summary response")
        summary_text = _normalized_string(parsed.get("summary_text")) or raw_content[:1200]
        generated_tags = _normalize_tags(parsed.get("generated_tags"), fallback_source=raw_content)
        grounded_claims = _normalize_grounded_claims(parsed.get("grounded_claims"))
        return SummaryArtifact(
            generated_category=_normalized_string(parsed.get("generated_category")) or "general",
            generated_tags=generated_tags,
            summary_text=summary_text,
            viewpoint_text=_normalized_string(parsed.get("viewpoint_text")),
            controversy_text=_normalized_string(parsed.get("controversy_text")),
            content_quality_score=_normalize_score(parsed.get("content_quality_score")),
            grounded_claims=grounded_claims,
            summary_segments=_normalize_summary_segments(
                parsed.get("summary_segments"),
                fallback_grounded_claims=grounded_claims,
            ),
            quality_meta={
                "provider": "openai-compatible",
                "model": self.model_name,
                "cleaning_level": cleaning_level or "unknown",
                "memory_context_count": len(related_items),
                "evidence_citation_count": len(evidence_citations),
                "related_context_count": len(related_items),
                "input_characters": len(raw_content),
            },
        )


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        batch_size = _resolve_embedding_batch_size(self.base_url)
        prepared_texts = [
            _prepare_embedding_input(text, base_url=self.base_url) for text in texts
        ]
        for batch in _chunk_texts(prepared_texts, size=batch_size):
            payload = {
                "model": self.model_name,
                "input": batch,
            }
            response_payload = _post_json(
                url=f"{self.base_url}/embeddings",
                api_key=self.api_key,
                timeout_seconds=self.timeout_seconds,
                payload=payload,
            )
            data = response_payload.get("data")
            if not isinstance(data, list):
                raise AppError(
                    status_code=502,
                    error_category="EMBEDDING_FAILED",
                    error_message="Embedding provider returned an invalid response payload.",
                )
            if len(data) != len(batch):
                raise AppError(
                    status_code=502,
                    error_category="EMBEDDING_FAILED",
                    error_message="Embedding provider returned a mismatched number of vectors.",
                )
            for item in data:
                if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                    raise AppError(
                        status_code=502,
                        error_category="EMBEDDING_FAILED",
                        error_message="Embedding provider response is missing embedding vectors.",
                    )
                vectors.append([float(value) for value in item["embedding"]])
        return vectors


class StubAnswerProvider:
    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name

    def answer(
        self,
        *,
        question: str,
        mode: str,
        evidence_citations: list[dict[str, object]],
        grounded_items: list[dict[str, object]],
    ) -> AnswerArtifact:
        if not evidence_citations:
            return AnswerArtifact(
                answer="未找到足够依据来回答该问题。",
                answer_status="insufficient_evidence",
                confidence=0.18,
                citation_ids=[],
                suggested_queries=_build_default_suggested_queries(question),
                quality_meta={
                    "provider": "stub-llm",
                    "model": self.model_name,
                    "evidence_citation_count": 0,
                    "grounded_item_count": len(grounded_items),
                },
            )

        lead_citation = evidence_citations[0]
        snippet = _normalized_string(lead_citation.get("snippet")) or _normalized_string(
            lead_citation.get("context_snippet")
        ) or "已找到相关依据。"
        answer_text = snippet[:240]
        if grounded_items:
            lead_claim = _normalized_string(grounded_items[0].get("claim"))
            if lead_claim:
                answer_text = f"{lead_claim}。补充证据：{answer_text}"
        answer_text = _format_stub_answer_by_mode(
            mode=mode,
            answer_text=answer_text,
            citation=lead_citation,
        )
        citation_id = _normalized_string(lead_citation.get("citation_id"))
        return AnswerArtifact(
            answer=answer_text,
            answer_status="grounded",
            confidence=0.72,
            citation_ids=[citation_id] if citation_id else [],
            suggested_queries=[],
            quality_meta={
                "provider": "stub-llm",
                "model": self.model_name,
                "evidence_citation_count": len(evidence_citations),
                "grounded_item_count": len(grounded_items),
            },
        )


class StubQueryRewriteProvider:
    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name

    def rewrite(
        self,
        *,
        question: str,
        mode: str,
        history: list[dict[str, object]],
        heuristic_rewrite: dict[str, object],
    ) -> QueryRewriteArtifact:
        del question, mode, history
        return _normalize_query_rewrite_artifact(
            heuristic_rewrite,
            strategy="heuristic",
        )


class OpenAICompatibleQueryRewriteProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def rewrite(
        self,
        *,
        question: str,
        mode: str,
        history: list[dict[str, object]],
        heuristic_rewrite: dict[str, object],
    ) -> QueryRewriteArtifact:
        history_context = "\n".join(
            f"- {str(item.get('role') or '')}: {str(item.get('content') or '')[:240]}"
            for item in history[-6:]
        ) or "- none"
        prompt = (
            "Rewrite a user's knowledge-base QA question for retrieval.\n"
            "Return strict JSON with keys: rewritten_question, requires_history, intent, risk_flags, confidence.\n"
            "Do not answer the question. Do not add facts that are not in the user question or conversation history.\n"
            "If the question is self-contained, keep rewritten_question unchanged and requires_history=false.\n"
            "If the question is a follow-up with pronouns or omitted topic, resolve only the missing topic from history.\n"
            "intent should be one of answer, knowledge_point, summary, source, follow_up, unknown.\n"
            "risk_flags must be an array of short strings such as uses_session_history, self_contained, ambiguous, intent_shift_risk.\n"
            "confidence must be a number between 0 and 1.\n"
            f"mode: {mode}\n"
            f"question: {question}\n"
            f"heuristic_rewrite: {json.dumps(heuristic_rewrite, ensure_ascii=False)}\n"
            f"history:\n{history_context}"
        )
        payload = {
            "model": self.model_name,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only a grounded query rewrite JSON object.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        response_payload = _post_json(
            url=f"{self.base_url}/chat/completions",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            payload=payload,
        )
        content = _extract_chat_completion_content(response_payload)
        parsed = _parse_json_object(content, context="llm query rewrite response")
        fallback = dict(heuristic_rewrite)
        fallback.update(parsed)
        return _normalize_query_rewrite_artifact(fallback, strategy="llm")


class OpenAICompatibleAnswerProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def answer(
        self,
        *,
        question: str,
        mode: str,
        evidence_citations: list[dict[str, object]],
        grounded_items: list[dict[str, object]],
    ) -> AnswerArtifact:
        evidence_context = "\n".join(
            (
                f"- citation_id: {str(citation.get('citation_id') or '')}\n"
                f"  title: {str(citation.get('title') or citation.get('source_name') or '')}\n"
                f"  section: {str(citation.get('section_title') or '')}\n"
                f"  snippet: {str(citation.get('snippet') or '')[:700]}\n"
                f"  context: {str(citation.get('expanded_context_snippet') or citation.get('context_snippet') or '')[:900]}"
            )
            for citation in evidence_citations[:5]
        ) or "- none"
        grounded_context = "\n".join(
            (
                f"- title: {str(item.get('title') or '')}\n"
                f"  category: {str(item.get('final_category') or '')}\n"
                f"  claim: {str(item.get('claim') or '')}\n"
                f"  evidence_titles: {', '.join(str(value) for value in item.get('evidence_titles', [])[:3])}"
            )
            for item in grounded_items[:5]
        ) or "- none"
        prompt = (
            "You answer questions against a personal knowledge base.\n"
            "Return strict JSON with keys: answer, answer_status, confidence, citation_ids, suggested_queries.\n"
            "answer_status must be one of grounded, insufficient_evidence, needs_clarification.\n"
            "confidence must be a number between 0 and 1.\n"
            "citation_ids must only reference ids present in evidence_context.\n"
            "Prefer evidence_context as the primary factual basis. Use grounded_items only as secondary background.\n"
            "If evidence is insufficient, set answer_status to insufficient_evidence and do not invent facts.\n"
            "If the question is too vague to answer well, set answer_status to needs_clarification and provide suggested_queries.\n"
            f"requested_mode: {mode}\n"
            f"{_build_answer_mode_instruction(mode)}\n"
            f"question: {question}\n\n"
            f"evidence_context:\n{evidence_context}\n\n"
            f"grounded_items:\n{grounded_context}\n"
        )
        payload = {
            "model": self.model_name,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Answer only from provided evidence. Be concise and grounded.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        response_payload = _post_json(
            url=f"{self.base_url}/chat/completions",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            payload=payload,
        )
        content = _extract_chat_completion_content(response_payload)
        parsed = _parse_json_object(content, context="llm answer response")
        return AnswerArtifact(
            answer=_normalized_string(parsed.get("answer")) or "未找到足够依据来回答该问题。",
            answer_status=_normalize_answer_status(parsed.get("answer_status")),
            confidence=_normalize_score(parsed.get("confidence")),
            citation_ids=_normalize_citation_id_list(parsed.get("citation_ids")),
            suggested_queries=_normalize_suggested_queries(
                parsed.get("suggested_queries"),
                question=question,
            ),
            quality_meta={
                "provider": "openai-compatible",
                "model": self.model_name,
                "evidence_citation_count": len(evidence_citations),
                "grounded_item_count": len(grounded_items),
            },
        )


def _build_answer_mode_instruction(mode: str) -> str:
    normalized_mode = (mode or "answer").strip().lower()
    if normalized_mode == "knowledge_point":
        return (
            "For knowledge_point mode, answer with the key fact directly. "
            "Prefer concise concept-level wording and include the exact term or mechanism when evidence provides it."
        )
    if normalized_mode == "summary":
        return (
            "For summary mode, synthesize the main takeaway from the evidence instead of only repeating one fragment. "
            "Keep it compact but complete."
        )
    if normalized_mode == "source":
        return (
            "For source mode, make the answer source-oriented. "
            "Name the relevant component, title, section, or evidence origin explicitly when it is available."
        )
    return (
        "For answer mode, answer the user question directly. "
        "Prefer a complete grounded answer over a bare fragment when the evidence is clear."
    )


def _format_stub_answer_by_mode(
    *,
    mode: str,
    answer_text: str,
    citation: dict[str, object],
) -> str:
    normalized_mode = (mode or "answer").strip().lower()
    if normalized_mode == "knowledge_point":
        return f"知识点：{answer_text}"
    if normalized_mode == "summary":
        return f"总结：{answer_text}"
    if normalized_mode == "source":
        title = _normalized_string(citation.get("title") or citation.get("source_name")) or "当前证据"
        section = _normalized_string(citation.get("section_title"))
        if section:
            return f"来源：{title} / {section}。{answer_text}"
        return f"来源：{title}。{answer_text}"
    return answer_text


def _normalize_query_rewrite_artifact(
    payload: object,
    *,
    strategy: str,
) -> QueryRewriteArtifact:
    raw = payload if isinstance(payload, dict) else {}
    rewritten_question = _normalized_string(raw.get("rewritten_question")) or _normalized_string(
        raw.get("question")
    ) or ""
    intent = _normalized_string(raw.get("intent")) or "unknown"
    risk_flags = _normalize_short_string_list(
        raw.get("risk_flags"),
        max_items=6,
        max_length=40,
    )
    requires_history = bool(raw.get("requires_history") or raw.get("used_history"))
    if requires_history and "uses_session_history" not in risk_flags:
        risk_flags.append("uses_session_history")
    if not requires_history and "self_contained" not in risk_flags:
        risk_flags.append("self_contained")
    return QueryRewriteArtifact(
        rewritten_question=rewritten_question,
        requires_history=requires_history,
        intent=intent,
        risk_flags=risk_flags[:6],
        confidence=_normalize_score(raw.get("confidence")),
        strategy=strategy,
    )


def _post_json(
    *,
    url: str,
    api_key: str,
    timeout_seconds: int,
    payload: dict[str, object],
) -> dict[str, object]:
    last_exception: Exception | None = None
    for attempt in range(1, _HTTP_RETRY_ATTEMPTS + 1):
        try:
            client = _get_http_client(url=url, api_key=api_key, timeout_seconds=timeout_seconds)
            response = client.post(url, json=payload)
            response.raise_for_status()
            parsed = response.json()
        except httpx.HTTPStatusError as exc:
            last_exception = exc
            if attempt < _HTTP_RETRY_ATTEMPTS and _should_retry_http_status(exc.response):
                time.sleep(_retry_backoff_seconds(attempt))
                continue
            detail = _extract_upstream_error_detail(exc.response)
            raise AppError(
                status_code=502,
                error_category="UPSTREAM_FAILED",
                error_message=f"Upstream provider request failed: {exc}. {detail}",
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exception = exc
            if attempt < _HTTP_RETRY_ATTEMPTS:
                time.sleep(_retry_backoff_seconds(attempt))
                continue
            raise AppError(
                status_code=502,
                error_category="UPSTREAM_FAILED",
                error_message=f"Upstream provider request failed: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                status_code=502,
                error_category="UPSTREAM_FAILED",
                error_message=f"Upstream provider request failed: {exc}",
            ) from exc
        except ValueError as exc:
            raise AppError(
                status_code=502,
                error_category="UPSTREAM_FAILED",
                error_message="Upstream provider returned invalid JSON.",
            ) from exc

        if not isinstance(parsed, dict):
            raise AppError(
                status_code=502,
                error_category="UPSTREAM_FAILED",
                error_message="Upstream provider returned an unexpected response shape.",
            )
        return parsed

    if last_exception is not None:
        raise AppError(
            status_code=502,
            error_category="UPSTREAM_FAILED",
            error_message=f"Upstream provider request failed: {last_exception}",
        ) from last_exception
    raise AppError(
        status_code=502,
        error_category="UPSTREAM_FAILED",
        error_message="Upstream provider request failed.",
    )


def _probe_llm_connection(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    timeout_seconds: int,
) -> None:
    _post_json(
        url=f"{base_url}/chat/completions",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        payload={
            "model": model_name,
            "messages": [{"role": "user", "content": "Reply with ok."}],
            "max_tokens": 1,
            "temperature": 0,
        },
    )


def _probe_embedding_connection(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    timeout_seconds: int,
) -> None:
    _post_json(
        url=f"{base_url}/embeddings",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        payload={
            "model": model_name,
            "input": ["connection test"],
        },
    )


def _normalize_llm_provider(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    return _LLM_PROVIDER_ALIASES.get(normalized, normalized)


def _normalize_embedding_provider(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    return _EMBEDDING_PROVIDER_ALIASES.get(normalized, normalized)


def _extract_upstream_error_detail(response: httpx.Response | None) -> str:
    if response is None:
        return "No upstream response body."
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("message", "error_msg", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return f"Response detail: {value.strip()}"
        error_value = payload.get("error")
        if isinstance(error_value, dict):
            message = error_value.get("message")
            if isinstance(message, str) and message.strip():
                return f"Response detail: {message.strip()}"
            code = error_value.get("code")
            if isinstance(code, str) and code.strip():
                return f"Response detail: code={code.strip()}"
    text = response.text.strip()
    if text:
        compact = " ".join(text.split())
        return f"Response detail: {compact[:300]}"
    return "No upstream response body."


def _resolve_embedding_batch_size(base_url: str) -> int:
    normalized = base_url.strip().lower()
    if "dashscope.aliyuncs.com" in normalized:
        return 10
    return 64


def _get_http_client(*, url: str, api_key: str, timeout_seconds: int) -> httpx.Client:
    key = (_client_cache_base_url(url), api_key, timeout_seconds)
    with _HTTP_CLIENT_CACHE_LOCK:
        client = _HTTP_CLIENT_CACHE.get(key)
        if client is None:
            client = httpx.Client(
                timeout=timeout_seconds,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            _HTTP_CLIENT_CACHE[key] = client
        return client


def _client_cache_base_url(url: str) -> str:
    normalized = url.rstrip("/")
    for suffix in ("/embeddings", "/chat/completions"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _should_retry_http_status(response: httpx.Response | None) -> bool:
    if response is None:
        return False
    return response.status_code in {408, 429} or response.status_code >= 500


def _retry_backoff_seconds(attempt: int) -> float:
    return _HTTP_RETRY_BACKOFF_SECONDS * attempt


def _resolve_embedding_input_char_limit(base_url: str) -> int:
    normalized = base_url.strip().lower()
    if "dashscope.aliyuncs.com" in normalized:
        return 2048
    return 8000


def _prepare_embedding_input(text: str, *, base_url: str) -> str:
    normalized_text = str(text or "")
    limit = _resolve_embedding_input_char_limit(base_url)
    truncated = normalized_text[:limit]
    if truncated:
        return truncated
    return " "


def _chunk_texts(texts: list[str], *, size: int) -> list[list[str]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [texts[index : index + size] for index in range(0, len(texts), size)]


def _missing_connectivity_fields(config: AppConfig, *, capability: str) -> list[str]:
    required: list[str] = []
    if capability == "llm":
        if not _normalized_string(config.llm_provider):
            required.append("llm_provider")
        if not _normalized_string(config.llm_model):
            required.append("llm_model")
    if capability == "embedding":
        if not _normalized_string(config.embedding_provider):
            required.append("embedding_provider")
        if not _normalized_string(config.embedding_model):
            required.append("embedding_model")

    for field in validate_provider_config(config):
        if capability == "llm" and field.startswith("llm_"):
            required.append(field)
        if capability == "embedding" and field.startswith("embedding_"):
            required.append(field)

    deduped: list[str] = []
    for field in required:
        if field not in deduped:
            deduped.append(field)
    return deduped


def _extract_chat_completion_content(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AppError(
            status_code=502,
            error_category="UPSTREAM_FAILED",
            error_message="Upstream provider response is missing choices.",
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise AppError(
            status_code=502,
            error_category="UPSTREAM_FAILED",
            error_message="Upstream provider response is missing a message payload.",
        )
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_blocks = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text_blocks.append(block["text"])
        if text_blocks:
            return "\n".join(text_blocks)
    raise AppError(
        status_code=502,
        error_category="UPSTREAM_FAILED",
        error_message="Upstream provider response does not contain usable content.",
    )


def _parse_json_object(content: str, *, context: str) -> dict[str, object]:
    stripped = content.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(stripped)
        if not match:
            raise AppError(
                status_code=502,
                error_category="UPSTREAM_FAILED",
                error_message=f"Failed to parse {context} as JSON.",
            )
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AppError(
                status_code=502,
                error_category="UPSTREAM_FAILED",
                error_message=f"Failed to parse {context} as JSON.",
            ) from exc
    if not isinstance(parsed, dict):
        raise AppError(
            status_code=502,
            error_category="UPSTREAM_FAILED",
            error_message=f"{context} is not a JSON object.",
        )
    return parsed


def _normalize_score(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, numeric))


def _normalized_string(value: object) -> str | None:
    if value is None:
        return None
    string_value = str(value).strip()
    return string_value or None


def _normalize_tags(value: object, *, fallback_source: str) -> list[str]:
    if isinstance(value, list):
        tags = [str(item).strip() for item in value if str(item).strip()]
        if tags:
            return tags[:6]
    return _extract_tags(title="", raw_content=fallback_source, source_type="text")


def _normalize_grounded_claims(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    normalized_claims: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        claim = _normalized_string(item.get("claim"))
        citation_ids_raw = item.get("citation_ids")
        if not claim or not isinstance(citation_ids_raw, list):
            continue
        citation_ids: list[str] = []
        for citation_id in citation_ids_raw:
            normalized_id = _normalized_string(citation_id)
            if normalized_id and normalized_id not in citation_ids:
                citation_ids.append(normalized_id)
        if citation_ids:
            normalized_claims.append(
                {
                    "claim": claim,
                    "citation_ids": citation_ids,
                }
            )
    return normalized_claims


def _normalize_answer_status(value: object) -> str:
    normalized = (_normalized_string(value) or "").lower()
    if normalized in {"grounded", "insufficient_evidence", "needs_clarification"}:
        return normalized
    return "insufficient_evidence"


def _normalize_citation_id_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    citation_ids: list[str] = []
    for entry in value:
        normalized = _normalized_string(entry)
        if normalized and normalized not in citation_ids:
            citation_ids.append(normalized)
    return citation_ids


def _build_default_suggested_queries(question: str) -> list[str]:
    normalized = (question or "").strip().rstrip("？?。.!")
    if not normalized:
        return ["请提供更具体的问题或关键词"]
    return [
        f"{normalized}（限定具体章节或标题）",
        f"{normalized}（限定来源类型或标签）",
    ]


def _normalize_suggested_queries(value: object, *, question: str) -> list[str]:
    if isinstance(value, list):
        normalized_queries: list[str] = []
        for entry in value:
            normalized = _normalized_string(entry)
            if normalized and normalized not in normalized_queries:
                normalized_queries.append(normalized)
        if normalized_queries:
            return normalized_queries[:3]
    return _build_default_suggested_queries(question)


def _normalize_summary_segments(
    value: object,
    *,
    fallback_grounded_claims: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    if isinstance(value, list):
        normalized_segments: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            text = _normalized_string(item.get("text"))
            citation_ids_raw = item.get("citation_ids")
            if not text or not isinstance(citation_ids_raw, list):
                continue
            citation_ids: list[str] = []
            for citation_id in citation_ids_raw:
                normalized_id = _normalized_string(citation_id)
                if normalized_id and normalized_id not in citation_ids:
                    citation_ids.append(normalized_id)
            if citation_ids:
                normalized_segments.append(
                    {
                        "text": text,
                        "citation_ids": citation_ids,
                    }
                )
        if normalized_segments:
            return normalized_segments

    fallback = fallback_grounded_claims or []
    return [
        {
            "text": str(item["claim"]),
            "citation_ids": list(item["citation_ids"]),
        }
        for item in fallback
        if isinstance(item, dict)
        and _normalized_string(item.get("claim"))
        and isinstance(item.get("citation_ids"), list)
    ]


def _extract_tags(*, title: str, raw_content: str, source_type: str) -> list[str]:
    frequencies: dict[str, int] = {}
    for token in _tokenize(f"{title} {raw_content}"):
        if token in _STOPWORDS:
            continue
        frequencies[token] = frequencies.get(token, 0) + 1
    ranked = sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))
    tags = [source_type]
    tags.extend(token for token, _ in ranked[:5])
    unique_tags: list[str] = []
    for tag in tags:
        if tag not in unique_tags:
            unique_tags.append(tag)
    return unique_tags[:6]


def _classify_text(raw_content: str, source_type: str) -> str:
    content = raw_content.lower()
    if any(keyword in content for keyword in ("python", "api", "backend", "frontend", "database")):
        return "engineering"
    if any(keyword in content for keyword in ("research", "study", "paper", "experiment")):
        return "research"
    if any(keyword in content for keyword in ("user", "customer", "product", "market")):
        return "product"
    return source_type if source_type != "text" else "general"


def _detect_controversy(raw_content: str) -> str | None:
    content = raw_content.lower()
    if any(keyword in content for keyword in ("however", "but", "risk", "trade-off", "controvers")):
        return "The source includes trade-offs or unresolved risks that deserve follow-up."
    return "No explicit controversy identified in the source."


def _split_sentences(raw_content: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+", raw_content.strip())
    return [part.strip() for part in parts if part.strip()]


def _tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _normalize_vector(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def _truncate_text(value: str, max_length: int) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length].rstrip()


def _normalize_short_string_list(
    value: object,
    *,
    max_items: int,
    max_length: int,
) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = _normalized_string(item)
        if not text:
            continue
        shortened = _truncate_text(text, max_length)
        if shortened and shortened not in normalized:
            normalized.append(shortened)
        if len(normalized) >= max_items:
            break
    return normalized


def _build_fallback_keywords(*, title: str, raw_content: str, source_type: str) -> list[dict[str, object]]:
    tags = _extract_tags(title=title, raw_content=raw_content, source_type=source_type)
    keywords: list[dict[str, object]] = []
    for index, tag in enumerate(tags[:_KEYWORDS_MAX_ITEMS]):
        keywords.append(
            {
                "keyword": tag,
                "weight": round(max(0.2, 1 - (index * 0.12)), 2),
            }
        )
    return keywords


def _normalize_keywords(value: object, *, fallback_title: str, fallback_source: str, source_type: str) -> list[dict[str, object]]:
    if isinstance(value, list):
        normalized: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            keyword = _normalized_string(item.get("keyword"))
            if not keyword:
                continue
            try:
                weight = float(item.get("weight"))
            except (TypeError, ValueError):
                weight = 0.5
            normalized.append(
                {
                    "keyword": _truncate_text(keyword, 40),
                    "weight": round(max(0.0, min(1.0, weight)), 2),
                }
            )
            if len(normalized) >= _KEYWORDS_MAX_ITEMS:
                break
        if normalized:
            return normalized
    return _build_fallback_keywords(
        title=fallback_title,
        raw_content=fallback_source,
        source_type=source_type,
    )


def _normalize_code_examples(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        language = _normalized_string(item.get("language")) or "text"
        snippet = _normalized_string(item.get("snippet"))
        if not snippet:
            continue
        normalized.append(
            {
                "language": _truncate_text(language, 20),
                "snippet": _truncate_text(snippet, _CODE_SNIPPET_MAX_LENGTH),
                "citation_ids": _normalize_citation_id_list(item.get("citation_ids")),
            }
        )
        if len(normalized) >= _CODE_EXAMPLES_MAX_ITEMS:
            break
    return normalized


def _summary_prompt_variant(raw_content: str) -> str:
    return "lightweight" if len((raw_content or "").strip()) < _LIGHTWEIGHT_SUMMARY_PROMPT_CHAR_THRESHOLD else "full"


def _build_summary_prompt(
    *,
    title: str,
    source_type: str,
    source_value: str,
    cleaning_level: str | None,
    raw_content: str,
    memory_context: str,
    evidence_context: str,
) -> tuple[str, str]:
    variant = _summary_prompt_variant(raw_content)
    base_header = (
        f"标题：{title}\n"
        f"源类型：{source_type}\n"
        f"源值：{source_value}\n"
        f"清洗级别：{cleaning_level or 'unknown'}\n\n"
        f"记忆上下文：\n{memory_context}\n\n"
        f"证据上下文：\n{evidence_context}\n\n"
        f"内容：\n{raw_content[:12000]}"
    )
    if variant == "lightweight":
        prompt = (
            "你要为个人研究档案库生成“简洁、证据优先”的 JSON 摘要，用于后续检索和快速回顾。\n"
            "你的输出必须是合法 JSON，不要在 JSON 外输出任何解释文字、Markdown 标题或代码围栏。\n"
            "顶层键固定为：generated_category, generated_tags, one_sentence_takeaway, summary_text, key_points, content_quality_score\n"
            "规则：\n"
            "1. 事实依据优先来自 evidence_context，证据不足时不要编造。\n"
            "2. memory_context 仅用于帮助理解背景，不作为事实来源。\n"
            "3. 若源内容以中文为主，所有自然语言字段使用简体中文。\n"
            "4. 风格要求：简洁、具体、高信息密度。\n"
            "字段要求：\n"
            "- one_sentence_takeaway: 一句话核心结论，建议 30 到 60 字。\n"
            "- summary_text: 不超过 120 字。\n"
            "- key_points: 2 到 4 条，每条不超过 50 字。\n"
            "- content_quality_score: 0 到 1，基于证据充分性和表达清晰度。\n\n"
            f"{base_header}"
        )
        return variant, prompt

    prompt = (
        "你要为个人研究档案库生成“学习型、证据优先”的结构化摘要。\n"
        "你的输出必须是合法 JSON。不要在 JSON 外输出解释、Markdown 标题、代码围栏（```）或注释。\n"
        "如果内容涉及代码，可以在 code_examples 字段中保留少量关键代码片段，但不要输出 fenced code block。\n"
        "顶层键固定为：generated_category, generated_tags, one_sentence_takeaway, summary_text, reading_focus, key_points, keywords, methods_or_process, pitfalls_or_limits, code_examples, content_quality_score, grounded_claims, summary_segments\n"
        "总体要求：\n"
        "1. 事实内容优先依据 evidence_context。\n"
        "2. memory_context 仅用于帮助理解术语、背景和主题关系，不作为事实引用来源。\n"
        "3. 如果证据不足，不要把内容写成确定事实。\n"
        "4. 不要捏造分类、标签、步骤、限制、关键词、代码示例或论断。\n"
        "5. 若源内容以中文为主，所有自然语言字段使用简体中文。\n"
        "6. 风格要求：紧凑、具体、高信息密度，面向快速学习与复盘。\n"
        "7. 所有 grounded_claims 和 summary_segments 的 citation_ids 必须来自 evidence_context 中真实存在的 citation_id。\n"
        "字段限制：summary_text <= 200 字；reading_focus 每条 <= 40 字；key_points 每条 <= 60 字；methods_or_process 与 pitfalls_or_limits 每条 <= 30 字；summary_segments 每条 <= 80 字。\n"
        "reading_focus 是“先看什么”的引导；key_points 是已经提炼出的具体知识点。\n"
        "keywords 的元素格式为 {\"keyword\": \"transformer\", \"weight\": 0.9}。\n"
        "grounded_claims 的元素格式为 {\"claim\": \"...\", \"citation_ids\": [\"cite-1\"]}。\n"
        "summary_segments 的元素格式为 {\"text\": \"...\", \"citation_ids\": [\"cite-1\"]}。\n"
        "content_quality_score = 证据充分性(0-0.4) + 逻辑一致性(0-0.3) + 来源可靠性(0-0.3)。\n"
        "如果内容过长，优先处理标题、导语、结论、小节标题附近内容和高信号段落。\n\n"
        f"{base_header}"
    )
    return variant, prompt


class StubSummaryProvider:
    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name

    def summarize(
        self,
        *,
        title: str,
        source_type: str,
        source_value: str,
        cleaning_level: str | None,
        raw_content: str,
        related_items: list[RelatedContextItem],
        evidence_citations: list[dict[str, object]],
    ) -> SummaryArtifact:
        sentences = _split_sentences(raw_content)
        summary_text = _truncate_text(raw_content, _SUMMARY_TEXT_MAX_LENGTH)
        one_sentence_takeaway = _truncate_text(sentences[0] if sentences else raw_content, _TAKEAWAY_MAX_LENGTH) or title
        reading_focus = _normalize_short_string_list(sentences[:3], max_items=3, max_length=_READING_FOCUS_MAX_LENGTH)
        key_points = _normalize_short_string_list(sentences[:4], max_items=4, max_length=_KEY_POINT_MAX_LENGTH)
        keywords = _build_fallback_keywords(title=title, raw_content=raw_content, source_type=source_type)
        grounded_claims = _normalize_grounded_claims(
            [
                {
                    "claim": _truncate_text(summary_text, 80),
                    "citation_ids": [str(evidence_citations[0].get("citation_id") or "").strip()],
                }
            ] if evidence_citations else []
        )
        summary_segments = _normalize_summary_segments(
            [
                {
                    "text": _truncate_text(summary_text, 80),
                    "citation_ids": [str(evidence_citations[0].get("citation_id") or "").strip()],
                }
            ] if evidence_citations else [],
            fallback_grounded_claims=grounded_claims,
        )
        quality_meta = {
            "provider": "stub-llm",
            "model": self.model_name,
            "source_value": source_value,
            "cleaning_level": cleaning_level or "unknown",
            "memory_context_count": len(related_items),
            "evidence_citation_count": len(evidence_citations),
            "related_context_count": len(related_items),
            "input_characters": len(raw_content),
            "prompt_variant": "stub",
            "one_sentence_takeaway": one_sentence_takeaway,
            "reading_focus": reading_focus,
            "key_points": key_points,
            "keywords": keywords,
            "methods_or_process": [],
            "pitfalls_or_limits": [],
            "code_examples": [],
        }
        return SummaryArtifact(
            generated_category="general",
            generated_tags=_extract_tags(title=title, raw_content=raw_content, source_type=source_type),
            one_sentence_takeaway=one_sentence_takeaway,
            summary_text=summary_text,
            viewpoint_text=one_sentence_takeaway,
            controversy_text=None,
            reading_focus=reading_focus,
            key_points=key_points,
            keywords=keywords,
            methods_or_process=[],
            pitfalls_or_limits=[],
            code_examples=[],
            content_quality_score=round(min(0.99, max(0.2, len(raw_content) / 4000)), 3),
            grounded_claims=grounded_claims,
            summary_segments=summary_segments,
            quality_meta=quality_meta,
        )


class OpenAICompatibleSummaryProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def summarize(
        self,
        *,
        title: str,
        source_type: str,
        source_value: str,
        cleaning_level: str | None,
        raw_content: str,
        related_items: list[RelatedContextItem],
        evidence_citations: list[dict[str, object]],
    ) -> SummaryArtifact:
        memory_context = "\n".join(
            (
                f"- title: {item.title}\n"
                f"  category: {item.final_category or 'unknown'}\n"
                f"  score: {item.score:.3f}\n"
                f"  summary: {item.summary_text[:500]}"
            )
            for item in related_items[:5]
        ) or "- none"
        evidence_context = "\n".join(
            (
                f"- citation_id: {str(citation.get('citation_id') or '')}\n"
                f"  title: {str(citation.get('title') or citation.get('source_name') or '')}\n"
                f"  section: {str(citation.get('section_title') or '')}\n"
                f"  snippet: {str(citation.get('snippet') or '')[:700]}\n"
                f"  context: {str(citation.get('expanded_context_snippet') or citation.get('context_snippet') or '')[:900]}"
            )
            for citation in evidence_citations[:5]
        ) or "- none"
        prompt_variant, prompt = _build_summary_prompt(
            title=title,
            source_type=source_type,
            source_value=source_value,
            cleaning_level=cleaning_level,
            raw_content=raw_content,
            memory_context=memory_context,
            evidence_context=evidence_context,
        )
        payload = {
            "model": self.model_name,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Produce concise, grounded study notes. Do not invent facts.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        response_payload = _post_json(
            url=f"{self.base_url}/chat/completions",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            payload=payload,
        )
        content = _extract_chat_completion_content(response_payload)
        parsed = _parse_json_object(content, context="llm summary response")

        one_sentence_takeaway = _truncate_text(
            _normalized_string(parsed.get("one_sentence_takeaway")) or title,
            _TAKEAWAY_MAX_LENGTH,
        )
        summary_text_limit = 120 if prompt_variant == "lightweight" else _SUMMARY_TEXT_MAX_LENGTH
        summary_text = _truncate_text(
            _normalized_string(parsed.get("summary_text")) or raw_content,
            summary_text_limit,
        )
        generated_tags = _normalize_tags(parsed.get("generated_tags"), fallback_source=raw_content)
        grounded_claims = _normalize_grounded_claims(parsed.get("grounded_claims"))
        reading_focus = _normalize_short_string_list(
            parsed.get("reading_focus") if prompt_variant == "full" else [],
            max_items=_READING_FOCUS_MAX_ITEMS,
            max_length=_READING_FOCUS_MAX_LENGTH,
        )
        key_points = _normalize_short_string_list(
            parsed.get("key_points"),
            max_items=_KEY_POINTS_MAX_ITEMS if prompt_variant == "full" else 4,
            max_length=_KEY_POINT_MAX_LENGTH if prompt_variant == "full" else 50,
        )
        keywords = _normalize_keywords(
            parsed.get("keywords") if prompt_variant == "full" else [],
            fallback_title=title,
            fallback_source=raw_content,
            source_type=source_type,
        )
        methods_or_process = _normalize_short_string_list(
            parsed.get("methods_or_process") if prompt_variant == "full" else [],
            max_items=_METHODS_MAX_ITEMS,
            max_length=_METHOD_STEP_MAX_LENGTH,
        )
        pitfalls_or_limits = _normalize_short_string_list(
            parsed.get("pitfalls_or_limits") if prompt_variant == "full" else [],
            max_items=_PITFALLS_MAX_ITEMS,
            max_length=_PITFALL_MAX_LENGTH,
        )
        code_examples = _normalize_code_examples(parsed.get("code_examples") if prompt_variant == "full" else [])
        summary_segments = _normalize_summary_segments(
            parsed.get("summary_segments"),
            fallback_grounded_claims=grounded_claims,
        )
        quality_meta = {
            "provider": "openai-compatible",
            "model": self.model_name,
            "cleaning_level": cleaning_level or "unknown",
            "memory_context_count": len(related_items),
            "evidence_citation_count": len(evidence_citations),
            "related_context_count": len(related_items),
            "input_characters": len(raw_content),
            "prompt_variant": prompt_variant,
            "one_sentence_takeaway": one_sentence_takeaway,
            "reading_focus": reading_focus,
            "key_points": key_points,
            "keywords": keywords,
            "methods_or_process": methods_or_process,
            "pitfalls_or_limits": pitfalls_or_limits,
            "code_examples": code_examples,
        }
        return SummaryArtifact(
            generated_category=_normalized_string(parsed.get("generated_category")) or "general",
            generated_tags=generated_tags,
            one_sentence_takeaway=one_sentence_takeaway,
            summary_text=summary_text,
            viewpoint_text=one_sentence_takeaway,
            controversy_text=pitfalls_or_limits[0] if pitfalls_or_limits else None,
            reading_focus=reading_focus,
            key_points=key_points,
            keywords=keywords,
            methods_or_process=methods_or_process,
            pitfalls_or_limits=pitfalls_or_limits,
            code_examples=code_examples,
            content_quality_score=_normalize_score(parsed.get("content_quality_score")),
            grounded_claims=grounded_claims,
            summary_segments=summary_segments,
            quality_meta=quality_meta,
        )


def _build_fallback_reader_guide_v2(
    *,
    title: str,
    raw_content: str,
    reading_focus: list[str],
    key_points: list[str],
    methods_or_process: list[str],
) -> dict[str, object]:
    sentences = _split_sentences(raw_content)
    return {
        "what_it_is": _truncate_text(sentences[0] if sentences else title, 120),
        "why_it_matters": _truncate_text(sentences[1] if len(sentences) > 1 else raw_content, 120),
        "how_to_apply": methods_or_process[:3],
        "core_concepts": key_points[:4],
        "study_path": reading_focus[:3],
    }


def _normalize_reader_guide_v2(
    value: object,
    *,
    title: str,
    raw_content: str,
    reading_focus: list[str],
    key_points: list[str],
    methods_or_process: list[str],
) -> dict[str, object]:
    fallback = _build_fallback_reader_guide_v2(
        title=title,
        raw_content=raw_content,
        reading_focus=reading_focus,
        key_points=key_points,
        methods_or_process=methods_or_process,
    )
    if not isinstance(value, dict):
        return fallback

    what_it_is = _truncate_text(
        _normalized_string(value.get("what_it_is")) or str(fallback["what_it_is"]),
        120,
    )
    why_it_matters = _truncate_text(
        _normalized_string(value.get("why_it_matters")) or str(fallback["why_it_matters"]),
        120,
    )
    how_to_apply = _normalize_short_string_list(
        value.get("how_to_apply") or fallback["how_to_apply"],
        max_items=5,
        max_length=40,
    )
    core_concepts = _normalize_short_string_list(
        value.get("core_concepts") or fallback["core_concepts"],
        max_items=6,
        max_length=50,
    )
    study_path = _normalize_short_string_list(
        value.get("study_path") or fallback["study_path"],
        max_items=4,
        max_length=40,
    )
    return {
        "what_it_is": what_it_is,
        "why_it_matters": why_it_matters,
        "how_to_apply": how_to_apply,
        "core_concepts": core_concepts,
        "study_path": study_path,
    }


def _build_summary_prompt(
    *,
    title: str,
    source_type: str,
    source_value: str,
    cleaning_level: str | None,
    raw_content: str,
    memory_context: str,
    evidence_context: str,
) -> tuple[str, str]:
    variant = _summary_prompt_variant(raw_content)
    base_header = (
        f"标题：{title}\n"
        f"源类型：{source_type}\n"
        f"源值：{source_value}\n"
        f"清洗级别：{cleaning_level or 'unknown'}\n\n"
        f"记忆上下文：\n{memory_context}\n\n"
        f"证据上下文：\n{evidence_context}\n\n"
        f"内容：\n{raw_content[:12000]}"
    )

    if variant == "lightweight":
        prompt = (
            "你要为个人研究档案库生成“简洁、证据优先”的 JSON 摘要，用于后续检索和快速回顾。\n"
            "你的输出必须是合法 JSON，不要在 JSON 外输出任何解释文字、Markdown 标题或代码围栏。\n"
            "顶层键固定为：generated_category, generated_tags, one_sentence_takeaway, summary_text, key_points, content_quality_score\n"
            "规则：\n"
            "1. 事实依据优先来自 evidence_context，证据不足时不要编造。\n"
            "2. memory_context 只用于帮助理解背景，不作为事实来源。\n"
            "3. 若源内容以中文为主，所有自然语言字段使用简体中文。\n"
            "4. 风格要求：简洁、具体、高信息密度。\n"
            "字段要求：\n"
            "- one_sentence_takeaway: 一句话核心结论，建议 30 到 60 字。\n"
            "- summary_text: 不超过 120 字。\n"
            "- key_points: 2 到 4 条，每条不超过 50 字。\n"
            "- content_quality_score: 0 到 1，基于证据充分性和表达清晰度。\n\n"
            f"{base_header}"
        )
        return variant, prompt

    prompt = (
        "你要为个人研究档案库生成“学习型、证据优先”的结构化摘要。\n"
        "你的输出必须是合法 JSON。不要在 JSON 外输出任何解释文字、Markdown 标题、代码围栏（```）或注释。\n"
        "如果内容涉及代码，可以在 code_examples 字段中保留少量关键代码片段，但不要输出 fenced code block。\n"
        "顶层键固定为：generated_category, generated_tags, one_sentence_takeaway, summary_text, reading_focus, key_points, keywords, reader_guide, methods_or_process, pitfalls_or_limits, code_examples, content_quality_score, grounded_claims, summary_segments\n"
        "总体要求：\n"
        "1. 事实内容优先依据 evidence_context。\n"
        "2. memory_context 仅用于帮助理解术语、背景和主题关系，不作为事实引用来源。\n"
        "3. 如果证据不足，不要把内容写成确定事实。\n"
        "4. 不要捏造分类、标签、步骤、限制、关键词、代码示例或论断。\n"
        "5. 若源内容以中文为主，所有自然语言字段使用简体中文。\n"
        "6. 风格要求：紧凑、具体、高信息密度，面向快速学习与复盘。\n"
        "7. reader_guide 用于人类阅读，重点回答“是什么、为什么、怎么学”，比 summary_text 更偏教学导读。\n"
        "8. 所有 grounded_claims 和 summary_segments 的 citation_ids 必须来自 evidence_context 中真实存在的 citation_id。\n"
        "字段长度限制：summary_text <= 200 字；reading_focus 每条 <= 40 字；key_points 每条 <= 60 字；reader_guide.what_it_is 和 reader_guide.why_it_matters 各 <= 120 字；reader_guide.how_to_apply 与 reader_guide.study_path 每条 <= 40 字；methods_or_process 与 pitfalls_or_limits 每条 <= 30 字；summary_segments 每条 <= 80 字。\n"
        "reading_focus 是“先看什么”的引导，key_points 是已经提炼出的具体知识点。\n"
        "keywords 的元素格式为 {\"keyword\": \"transformer\", \"weight\": 0.9}。\n"
        "reader_guide 的格式为 {\"what_it_is\": \"...\", \"why_it_matters\": \"...\", \"how_to_apply\": [\"...\"], \"core_concepts\": [\"...\"], \"study_path\": [\"...\"]}。\n"
        "grounded_claims 的元素格式为 {\"claim\": \"...\", \"citation_ids\": [\"cite-1\"]}。\n"
        "summary_segments 的元素格式为 {\"text\": \"...\", \"citation_ids\": [\"cite-1\"]}。\n"
        "content_quality_score = 证据充分性(0-0.4) + 逻辑一致性(0-0.3) + 来源可靠性(0-0.3)。\n"
        "如果内容过长，优先处理标题、导语、结论、小节标题附近内容和高信号段落。\n\n"
        f"{base_header}"
    )
    return variant, prompt


class StubSummaryProvider:
    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name

    def summarize(
        self,
        *,
        title: str,
        source_type: str,
        source_value: str,
        cleaning_level: str | None,
        raw_content: str,
        related_items: list[RelatedContextItem],
        evidence_citations: list[dict[str, object]],
    ) -> SummaryArtifact:
        sentences = _split_sentences(raw_content)
        summary_text = _truncate_text(raw_content, _SUMMARY_TEXT_MAX_LENGTH)
        one_sentence_takeaway = (
            _truncate_text(sentences[0] if sentences else raw_content, _TAKEAWAY_MAX_LENGTH) or title
        )
        reading_focus = _normalize_short_string_list(
            sentences[:3],
            max_items=3,
            max_length=_READING_FOCUS_MAX_LENGTH,
        )
        key_points = _normalize_short_string_list(
            sentences[:4],
            max_items=4,
            max_length=_KEY_POINT_MAX_LENGTH,
        )
        keywords = _build_fallback_keywords(title=title, raw_content=raw_content, source_type=source_type)
        methods_or_process = _normalize_short_string_list(
            [sentence for sentence in sentences if any(token in sentence for token in ("先", "然后", "最后", "步骤", "配置"))],
            max_items=3,
            max_length=_METHOD_STEP_MAX_LENGTH,
        )
        reader_guide = _normalize_reader_guide_v2(
            None,
            title=title,
            raw_content=raw_content,
            reading_focus=reading_focus,
            key_points=key_points,
            methods_or_process=methods_or_process,
        )
        grounded_claims = _normalize_grounded_claims(
            [
                {
                    "claim": _truncate_text(summary_text, 80),
                    "citation_ids": [str(evidence_citations[0].get("citation_id") or "").strip()],
                }
            ]
            if evidence_citations
            else []
        )
        summary_segments = _normalize_summary_segments(
            [
                {
                    "text": _truncate_text(summary_text, 80),
                    "citation_ids": [str(evidence_citations[0].get("citation_id") or "").strip()],
                }
            ]
            if evidence_citations
            else [],
            fallback_grounded_claims=grounded_claims,
        )
        quality_meta = {
            "provider": "stub-llm",
            "model": self.model_name,
            "source_value": source_value,
            "cleaning_level": cleaning_level or "unknown",
            "memory_context_count": len(related_items),
            "evidence_citation_count": len(evidence_citations),
            "related_context_count": len(related_items),
            "input_characters": len(raw_content),
            "prompt_variant": "stub",
            "one_sentence_takeaway": one_sentence_takeaway,
            "reading_focus": reading_focus,
            "key_points": key_points,
            "keywords": keywords,
            "reader_guide": reader_guide,
            "methods_or_process": methods_or_process,
            "pitfalls_or_limits": [],
            "code_examples": [],
        }
        return SummaryArtifact(
            generated_category="general",
            generated_tags=_extract_tags(title=title, raw_content=raw_content, source_type=source_type),
            one_sentence_takeaway=one_sentence_takeaway,
            summary_text=summary_text,
            viewpoint_text=one_sentence_takeaway,
            controversy_text=None,
            reading_focus=reading_focus,
            key_points=key_points,
            keywords=keywords,
            methods_or_process=methods_or_process,
            pitfalls_or_limits=[],
            code_examples=[],
            content_quality_score=round(min(0.99, max(0.2, len(raw_content) / 4000)), 3),
            grounded_claims=grounded_claims,
            summary_segments=summary_segments,
            quality_meta=quality_meta,
        )


class OpenAICompatibleSummaryProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def summarize(
        self,
        *,
        title: str,
        source_type: str,
        source_value: str,
        cleaning_level: str | None,
        raw_content: str,
        related_items: list[RelatedContextItem],
        evidence_citations: list[dict[str, object]],
    ) -> SummaryArtifact:
        memory_context = "\n".join(
            (
                f"- title: {item.title}\n"
                f"  category: {item.final_category or 'unknown'}\n"
                f"  score: {item.score:.3f}\n"
                f"  summary: {item.summary_text[:500]}"
            )
            for item in related_items[:5]
        ) or "- none"
        evidence_context = "\n".join(
            (
                f"- citation_id: {str(citation.get('citation_id') or '')}\n"
                f"  title: {str(citation.get('title') or citation.get('source_name') or '')}\n"
                f"  section: {str(citation.get('section_title') or '')}\n"
                f"  snippet: {str(citation.get('snippet') or '')[:700]}\n"
                f"  context: {str(citation.get('expanded_context_snippet') or citation.get('context_snippet') or '')[:900]}"
            )
            for citation in evidence_citations[:5]
        ) or "- none"
        prompt_variant, prompt = _build_summary_prompt(
            title=title,
            source_type=source_type,
            source_value=source_value,
            cleaning_level=cleaning_level,
            raw_content=raw_content,
            memory_context=memory_context,
            evidence_context=evidence_context,
        )
        payload = {
            "model": self.model_name,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Produce concise, grounded study notes. Do not invent facts.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        response_payload = _post_json(
            url=f"{self.base_url}/chat/completions",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            payload=payload,
        )
        content = _extract_chat_completion_content(response_payload)
        parsed = _parse_json_object(content, context="llm summary response")

        one_sentence_takeaway = _truncate_text(
            _normalized_string(parsed.get("one_sentence_takeaway")) or title,
            _TAKEAWAY_MAX_LENGTH,
        )
        summary_text_limit = 120 if prompt_variant == "lightweight" else _SUMMARY_TEXT_MAX_LENGTH
        summary_text = _truncate_text(
            _normalized_string(parsed.get("summary_text")) or raw_content,
            summary_text_limit,
        )
        generated_tags = _normalize_tags(parsed.get("generated_tags"), fallback_source=raw_content)
        grounded_claims = _normalize_grounded_claims(parsed.get("grounded_claims"))
        reading_focus = _normalize_short_string_list(
            parsed.get("reading_focus") if prompt_variant == "full" else [],
            max_items=_READING_FOCUS_MAX_ITEMS,
            max_length=_READING_FOCUS_MAX_LENGTH,
        )
        key_points = _normalize_short_string_list(
            parsed.get("key_points"),
            max_items=_KEY_POINTS_MAX_ITEMS if prompt_variant == "full" else 4,
            max_length=_KEY_POINT_MAX_LENGTH if prompt_variant == "full" else 50,
        )
        keywords = _normalize_keywords(
            parsed.get("keywords") if prompt_variant == "full" else [],
            fallback_title=title,
            fallback_source=raw_content,
            source_type=source_type,
        )
        methods_or_process = _normalize_short_string_list(
            parsed.get("methods_or_process") if prompt_variant == "full" else [],
            max_items=_METHODS_MAX_ITEMS,
            max_length=_METHOD_STEP_MAX_LENGTH,
        )
        pitfalls_or_limits = _normalize_short_string_list(
            parsed.get("pitfalls_or_limits") if prompt_variant == "full" else [],
            max_items=_PITFALLS_MAX_ITEMS,
            max_length=_PITFALL_MAX_LENGTH,
        )
        code_examples = _normalize_code_examples(parsed.get("code_examples") if prompt_variant == "full" else [])
        reader_guide = _normalize_reader_guide_v2(
            parsed.get("reader_guide") if prompt_variant == "full" else None,
            title=title,
            raw_content=raw_content,
            reading_focus=reading_focus,
            key_points=key_points,
            methods_or_process=methods_or_process,
        )
        summary_segments = _normalize_summary_segments(
            parsed.get("summary_segments"),
            fallback_grounded_claims=grounded_claims,
        )
        quality_meta = {
            "provider": "openai-compatible",
            "model": self.model_name,
            "cleaning_level": cleaning_level or "unknown",
            "memory_context_count": len(related_items),
            "evidence_citation_count": len(evidence_citations),
            "related_context_count": len(related_items),
            "input_characters": len(raw_content),
            "prompt_variant": prompt_variant,
            "one_sentence_takeaway": one_sentence_takeaway,
            "reading_focus": reading_focus,
            "key_points": key_points,
            "keywords": keywords,
            "reader_guide": reader_guide,
            "methods_or_process": methods_or_process,
            "pitfalls_or_limits": pitfalls_or_limits,
            "code_examples": code_examples,
        }
        return SummaryArtifact(
            generated_category=_normalized_string(parsed.get("generated_category")) or "general",
            generated_tags=generated_tags,
            one_sentence_takeaway=one_sentence_takeaway,
            summary_text=summary_text,
            viewpoint_text=one_sentence_takeaway,
            controversy_text=pitfalls_or_limits[0] if pitfalls_or_limits else None,
            reading_focus=reading_focus,
            key_points=key_points,
            keywords=keywords,
            methods_or_process=methods_or_process,
            pitfalls_or_limits=pitfalls_or_limits,
            code_examples=code_examples,
            content_quality_score=_normalize_score(parsed.get("content_quality_score")),
            grounded_claims=grounded_claims,
            summary_segments=summary_segments,
            quality_meta=quality_meta,
        )
