from __future__ import annotations

import re
from dataclasses import dataclass

from backend.app.utils import new_id


_HEADING_RE = re.compile(r"^(#{1,6}|[0-9]+[.)])\s+(?P<title>.+)$")


@dataclass(slots=True)
class DocumentChunk:
    id: str
    knowledge_item_id: str
    parent_chunk_id: str | None
    chunk_level: str
    section_title: str | None
    content: str
    position: int
    token_estimate: int


def build_document_chunks(
    *,
    knowledge_item_id: str,
    raw_content: str,
    parent_target_chars: int = 1600,
    child_target_chars: int = 500,
    child_overlap_chars: int = 80,
) -> tuple[list[DocumentChunk], list[DocumentChunk]]:
    parent_sections = _split_parent_sections(raw_content, target_chars=parent_target_chars)
    parent_chunks: list[DocumentChunk] = []
    child_chunks: list[DocumentChunk] = []

    for parent_position, section in enumerate(parent_sections):
        parent_chunk = DocumentChunk(
            id=new_id(),
            knowledge_item_id=knowledge_item_id,
            parent_chunk_id=None,
            chunk_level="parent",
            section_title=section["section_title"],
            content=section["content"],
            position=parent_position,
            token_estimate=_estimate_tokens(section["content"]),
        )
        parent_chunks.append(parent_chunk)

        child_windows = _split_child_windows(
            section["content"],
            target_chars=child_target_chars,
            overlap_chars=child_overlap_chars,
        )
        for child_position, content in enumerate(child_windows):
            child_chunks.append(
                DocumentChunk(
                    id=new_id(),
                    knowledge_item_id=knowledge_item_id,
                    parent_chunk_id=parent_chunk.id,
                    chunk_level="child",
                    section_title=section["section_title"],
                    content=content,
                    position=child_position,
                    token_estimate=_estimate_tokens(content),
                )
            )

    return parent_chunks, child_chunks


def _split_parent_sections(
    raw_content: str,
    *,
    target_chars: int,
) -> list[dict[str, str | None]]:
    blocks = [block.strip() for block in raw_content.split("\n\n") if block.strip()]
    if not blocks:
        content = raw_content.strip()
        return [{"section_title": None, "content": content}] if content else []

    sections: list[dict[str, str | None]] = []
    current_title: str | None = None
    current_parts: list[str] = []

    for block in blocks:
        lines = block.splitlines()
        first_line = lines[0].strip() if lines else ""
        heading_match = _HEADING_RE.match(first_line)
        if heading_match:
            if current_parts:
                sections.append(
                    {
                        "section_title": current_title,
                        "content": "\n\n".join(current_parts).strip(),
                    }
                )
                current_parts = []
            current_title = heading_match.group("title").strip()
            remaining = "\n".join(lines[1:]).strip()
            if remaining:
                current_parts.append(remaining)
            continue

        current_parts.append(block)
        if len("\n\n".join(current_parts)) >= target_chars:
            sections.append(
                {
                    "section_title": current_title,
                    "content": "\n\n".join(current_parts).strip(),
                }
            )
            current_title = None
            current_parts = []

    if current_parts:
        sections.append(
            {
                "section_title": current_title,
                "content": "\n\n".join(current_parts).strip(),
            }
        )

    return [section for section in sections if str(section["content"]).strip()]


def _split_child_windows(
    content: str,
    *,
    target_chars: int,
    overlap_chars: int,
) -> list[str]:
    stripped = content.strip()
    if not stripped:
        return []
    if len(stripped) <= target_chars:
        return [stripped]

    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        end = min(start + target_chars, len(stripped))
        window = stripped[start:end].strip()
        if window:
            chunks.append(window)
        if end >= len(stripped):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _estimate_tokens(content: str) -> int:
    return max(1, len(content) // 4)
