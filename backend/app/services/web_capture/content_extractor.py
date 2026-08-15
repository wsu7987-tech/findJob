from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from bs4 import BeautifulSoup
from bs4.element import Tag


PreviewContentType = Literal["markdown", "text"]

_UNWANTED_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside")
_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "figcaption", "blockquote", "pre")
_CONTAINER_TAGS = ("div", "section")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MARKDOWN_FENCE_BLOCK_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_MARKDOWN_FENCE_CONTENT_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_PAGE_CHAR_LIMIT = 1800
_PREVIEW_CHAR_LIMIT = 4000


@dataclass(slots=True)
class ExtractedPreviewPage:
    page_number: int
    content_type: PreviewContentType
    content: str


@dataclass(slots=True)
class ExtractedDocument:
    url: str
    title: str
    markdown_text: str
    raw_text: str
    preview_text: str
    preview_pages: list[ExtractedPreviewPage]


def extract_rendered_document(
    *,
    url: str,
    title: str,
    rendered_html: str,
    ocr_segments: list[dict[str, Any]] | list[Any],
) -> ExtractedDocument:
    soup = BeautifulSoup(rendered_html, "html.parser")
    for node in soup(_UNWANTED_TAGS):
        node.decompose()

    content_root = soup.find("article") or soup.find("main") or soup.body or soup
    extracted_title = _choose_title(soup, content_root, title, url)
    blocks = _collect_blocks(content_root, extracted_title)

    markdown_parts: list[str] = []
    if extracted_title:
        markdown_parts.append(f"# {extracted_title}")
    markdown_parts.extend(blocks)

    markdown_text = _normalize_markdown("\n\n".join(part for part in markdown_parts if part.strip()))
    markdown_text = merge_ocr_segments(markdown_text=markdown_text, ocr_segments=ocr_segments)
    raw_text = _markdown_to_plain_text(markdown_text)
    preview_pages = paginate_markdown(markdown_text)
    preview_text = markdown_text[:_PREVIEW_CHAR_LIMIT]

    return ExtractedDocument(
        url=url,
        title=extracted_title,
        markdown_text=markdown_text,
        raw_text=raw_text,
        preview_text=preview_text,
        preview_pages=preview_pages,
    )


def merge_ocr_segments(
    *,
    markdown_text: str,
    ocr_segments: list[dict[str, Any]] | list[Any],
) -> str:
    blocks = _split_markdown_blocks(markdown_text)
    if not blocks or not ocr_segments:
        return _normalize_markdown(markdown_text)

    for segment in ocr_segments:
        anchor_text = _segment_value(segment, "anchor_text")
        text = _segment_value(segment, "text")
        if not text:
            continue

        inserted = False
        if anchor_text:
            for index, block in enumerate(blocks):
                if anchor_text in block:
                    if text not in block:
                        blocks[index] = f"{block.rstrip()}\n\n{text.strip()}"
                    inserted = True
                    break

        if not inserted:
            blocks.append(text.strip())

    return _normalize_markdown("\n\n".join(blocks))


def paginate_markdown(markdown_text: str) -> list[ExtractedPreviewPage]:
    normalized = _normalize_markdown(markdown_text)
    if not normalized:
        return []

    blocks = _split_markdown_blocks(normalized)
    if not blocks:
        blocks = [normalized]

    pages: list[str] = []
    current = ""
    for block in blocks:
        for fragment in _split_page_block(block):
            if not current:
                current = fragment
                continue

            candidate = f"{current}\n\n{fragment}"
            if len(candidate) > _PAGE_CHAR_LIMIT:
                pages.append(current)
                current = fragment
            else:
                current = candidate

    if current:
        pages.append(current)

    return [
        ExtractedPreviewPage(page_number=index + 1, content_type="markdown", content=page)
        for index, page in enumerate(pages)
    ]


def _choose_title(soup: BeautifulSoup, content_root: Tag, fallback_title: str, url: str) -> str:
    for candidate in (
        _first_heading(content_root),
        _soup_title(soup),
        fallback_title,
        url,
    ):
        if candidate and candidate.strip():
            return candidate.strip()[:200]
    return "Untitled item"


def _collect_blocks(content_root: Tag, extracted_title: str) -> list[str]:
    blocks: list[str] = []
    heading_skipped = False
    for tag in content_root.find_all(_BLOCK_TAGS):
        if _has_block_ancestor(tag):
            continue

        text = _tag_to_markdown(tag)
        if not text:
            continue

        if not heading_skipped and extracted_title and text == f"# {extracted_title}":
            heading_skipped = True
            continue

        blocks.append(text)

    return _merge_container_blocks(content_root, blocks)


def _merge_container_blocks(content_root: Tag, blocks: list[str]) -> list[str]:
    merged = list(blocks)
    for tag in content_root.find_all(_CONTAINER_TAGS):
        if _has_block_ancestor(tag):
            continue
        if tag.find(_BLOCK_TAGS):
            continue
        text = _normalize_inline_text(tag.get_text(" ", strip=True))
        if not text:
            continue
        if text not in merged:
            merged.append(text)
    return merged


def _tag_to_markdown(tag: Tag) -> str:
    if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(tag.name[1])
        text = _normalize_inline_text(tag.get_text(" ", strip=True))
        return f"{'#' * level} {text}" if text else ""
    if tag.name == "li":
        text = _normalize_inline_text(tag.get_text(" ", strip=True))
        return f"- {text}" if text else ""
    if tag.name == "blockquote":
        text = _normalize_inline_text(tag.get_text("\n", strip=True))
        return "\n".join(f"> {line}" for line in text.splitlines() if line.strip())
    if tag.name == "pre":
        text = tag.get_text("\n", strip=False).strip("\n")
        return f"```\n{text}\n```" if text else ""
    if tag.name == "figcaption":
        text = _normalize_inline_text(tag.get_text(" ", strip=True))
        return text
    text = _normalize_inline_text(tag.get_text(" ", strip=True))
    return text


def _has_block_ancestor(tag: Tag) -> bool:
    parent = tag.parent
    while isinstance(parent, Tag):
        if parent.name in _BLOCK_TAGS:
            return True
        parent = parent.parent
    return False


def _first_heading(root: Tag) -> str | None:
    for tag_name in ("h1", "h2", "h3"):
        candidate = root.find(tag_name)
        if candidate is None:
            continue
        text = _normalize_inline_text(candidate.get_text(" ", strip=True))
        if text:
            return text
    return None


def _soup_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        return _normalize_inline_text(soup.title.string)
    return None


def _split_markdown_blocks(markdown_text: str) -> list[str]:
    return [block.strip() for block in _normalize_markdown(markdown_text).split("\n\n") if block.strip()]


def _markdown_to_plain_text(markdown_text: str) -> str:
    protected_blocks: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        protected_blocks.append(match.group(1).strip("\n"))
        return f"__RAW_CODE_BLOCK_{len(protected_blocks) - 1}__"

    text = _MARKDOWN_FENCE_CONTENT_RE.sub(_stash, markdown_text)
    text = _MARKDOWN_IMAGE_RE.sub(r"\1", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _MARKDOWN_INLINE_CODE_RE.sub(r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}-\s+", "", text, flags=re.MULTILINE)
    text = _normalize_plain_text(text)
    for index, block in enumerate(protected_blocks):
        text = text.replace(f"__RAW_CODE_BLOCK_{index}__", block)
    return _normalize_plain_text(text)


def _normalize_inline_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    return normalized.strip()


def _normalize_markdown(text: str) -> str:
    protected_blocks: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        protected_blocks.append(match.group(0))
        return f"__MARKDOWN_FENCE_{len(protected_blocks) - 1}__"

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _MARKDOWN_FENCE_BLOCK_RE.sub(_stash, normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    for index, block in enumerate(protected_blocks):
        normalized = normalized.replace(f"__MARKDOWN_FENCE_{index}__", block)
    return normalized.strip()


def _normalize_plain_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+$", "", normalized, flags=re.MULTILINE)
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    return normalized.strip()


def _split_page_block(block: str) -> list[str]:
    if len(block) <= _PAGE_CHAR_LIMIT:
        return [block]
    return _split_long_text(block)


def _split_long_text(text: str) -> list[str]:
    return [text[index : index + _PAGE_CHAR_LIMIT] for index in range(0, len(text), _PAGE_CHAR_LIMIT)]


def _segment_value(segment: dict[str, Any] | Any, key: str) -> str:
    if isinstance(segment, dict):
        value = segment.get(key)
    else:
        value = getattr(segment, key, None)
    return "" if value is None else str(value).strip()
