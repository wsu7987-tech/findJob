from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from backend.app.config import AppConfig
from backend.app.errors import AppError


_MAX_EXTRACTED_CHARS = 120_000
_MIN_TEXT_CHARS = 5
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MARKDOWN_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MARKDOWN_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MARKDOWN_FRONT_MATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(slots=True)
class IngestedContent:
    normalized_source_value: str
    title: str
    source_name: str
    raw_content: str


def ingest_source(
    config: AppConfig,
    *,
    source_type: str,
    source_value: str,
    raw_text: str | None,
    title: str | None,
) -> IngestedContent:
    if raw_text and raw_text.strip():
        return _ingest_inline_override(
            source_type=source_type,
            source_value=source_value,
            raw_text=raw_text,
            title=title,
        )

    if source_type == "pdf":
        file_path = _resolve_existing_path(source_value)
        final_title = _choose_title(title, file_path.stem, file_path.name)
        return IngestedContent(
            normalized_source_value=str(file_path),
            title=final_title,
            source_name=file_path.name,
            raw_content="",
        )

    if source_type == "markdown":
        return _ingest_markdown_source(source_value, raw_text, title)

    if source_type == "text":
        return _ingest_text_source(source_value, raw_text, title)

    raise AppError(
        status_code=400,
        error_category="VALIDATION_FAILED",
        error_message=f"Unsupported source_type: {source_type}",
    )


def _ingest_inline_override(
    *,
    source_type: str,
    source_value: str,
    raw_text: str,
    title: str | None,
) -> IngestedContent:
    normalized_source_value = _normalize_source_reference(source_type, source_value)
    extracted_text = _normalize_text(raw_text)
    extracted_title = (
        _extract_markdown_title(raw_text) if source_type == "markdown" else _first_line(extracted_text)
    )

    source_name = _source_name_for(source_type, normalized_source_value)
    final_title = _choose_title(title, extracted_title, source_name)
    _ensure_meaningful_text(extracted_text, source_name)
    return IngestedContent(
        normalized_source_value=normalized_source_value,
        title=final_title,
        source_name=source_name,
        raw_content=extracted_text,
    )


def _ingest_markdown_source(
    source_value: str,
    raw_text: str | None,
    title: str | None,
) -> IngestedContent:
    if raw_text and raw_text.strip():
        markdown_source = raw_text
        normalized_source_value = source_value.strip() or "inline-markdown"
        source_name = normalized_source_value
    else:
        maybe_path = _try_resolve_path(source_value)
        if maybe_path is not None and maybe_path.exists():
            markdown_source = maybe_path.read_text(encoding="utf-8")
            normalized_source_value = str(maybe_path)
            source_name = maybe_path.name
        else:
            markdown_source = source_value
            normalized_source_value = source_value.strip() or "inline-markdown"
            source_name = normalized_source_value

    plain_text = _markdown_to_text(markdown_source)
    extracted_title = _extract_markdown_title(markdown_source)
    final_title = _choose_title(title, extracted_title, source_name)
    return IngestedContent(
        normalized_source_value=normalized_source_value,
        title=final_title,
        source_name=source_name,
        raw_content=plain_text,
    )


def _ingest_text_source(
    source_value: str,
    raw_text: str | None,
    title: str | None,
) -> IngestedContent:
    if raw_text and raw_text.strip():
        text = _normalize_text(raw_text)
        normalized_source_value = source_value.strip() or "inline-text"
        source_name = normalized_source_value
    else:
        maybe_path = _try_resolve_path(source_value)
        if maybe_path is not None and maybe_path.exists() and maybe_path.is_file():
            text = _normalize_text(maybe_path.read_text(encoding="utf-8"))
            normalized_source_value = str(maybe_path)
            source_name = maybe_path.name
        else:
            text = _normalize_text(source_value)
            normalized_source_value = source_value.strip() or "inline-text"
            source_name = normalized_source_value

    _ensure_meaningful_text(text, source_name)
    final_title = _choose_title(title, _first_line(text), source_name)
    return IngestedContent(
        normalized_source_value=normalized_source_value,
        title=final_title,
        source_name=source_name,
        raw_content=text,
    )


def _markdown_to_text(
    markdown_source: str,
    *,
    max_chars: int | None = _MAX_EXTRACTED_CHARS,
) -> str:
    text = _MARKDOWN_FRONT_MATTER_RE.sub("", markdown_source)
    text = _MARKDOWN_CODE_FENCE_RE.sub(" ", text)
    text = _MARKDOWN_IMAGE_RE.sub(r"\1", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _MARKDOWN_INLINE_CODE_RE.sub(r"\1", text)
    text = _MARKDOWN_HEADER_RE.sub("", text)
    # 保留列表语义，单独移除 Markdown 分隔线，避免把项目经历中的短横线内容抹掉。
    text = re.sub(r"(?m)^\s*[-*+]\s+", "• ", text)
    text = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", " ", text)
    text = re.sub(r"[*_>#]{1,3}", " ", text)
    return _normalize_text(text, max_chars=max_chars)


def _extract_markdown_title(markdown_source: str) -> str | None:
    for line in markdown_source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            candidate = stripped.lstrip("#").strip()
            if candidate:
                return candidate
    return None


def _resolve_existing_path(source_value: str) -> Path:
    path = _try_resolve_path(source_value)
    if path is None or not path.exists() or not path.is_file():
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message=f"File not found: {source_value}",
        )
    return path


def _try_resolve_path(source_value: str) -> Path | None:
    stripped = source_value.strip()
    if not stripped:
        return None
    try:
        return Path(stripped).expanduser().resolve(strict=False)
    except OSError:
        return None


def _choose_title(*candidates: str | None) -> str:
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()[:200]
    return "Untitled item"


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None


def _normalize_text(
    text: str,
    *,
    max_chars: int | None = _MAX_EXTRACTED_CHARS,
) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    normalized = normalized.strip()
    return normalized if max_chars is None else normalized[:max_chars]


def _ensure_meaningful_text(text: str, source_name: str) -> None:
    if len(text.strip()) < _MIN_TEXT_CHARS:
        raise AppError(
            status_code=400,
            error_category="INGEST_FAILED",
            error_message=f"Extracted content is too short to summarize: {source_name}",
        )


def _normalize_source_reference(source_type: str, source_value: str) -> str:
    maybe_path = _try_resolve_path(source_value)
    if maybe_path is not None and maybe_path.exists():
        return str(maybe_path)
    stripped = source_value.strip()
    return stripped or f"inline-{source_type}"


def _source_name_for(source_type: str, normalized_source_value: str) -> str:
    maybe_path = _try_resolve_path(normalized_source_value)
    if maybe_path is not None and maybe_path.suffix:
        return maybe_path.name
    return normalized_source_value
