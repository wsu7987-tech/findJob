from __future__ import annotations

from pathlib import Path

from backend.app.services.ai import RelatedContextItem, SummaryArtifact
from backend.app.services.evidence_bundle import build_summary_relation_meta
from backend.app.utils import safe_filename_slug, slugify


def _append_reader_guide_markdown(markdown_content: str, summary: SummaryArtifact) -> str:
    quality_meta = summary.quality_meta or {}
    reader_guide = quality_meta.get("reader_guide")
    if not isinstance(reader_guide, dict):
        return markdown_content
    if "## 学习导读" in markdown_content:
        return markdown_content

    what_it_is = str(reader_guide.get("what_it_is") or "").strip()
    why_it_matters = str(reader_guide.get("why_it_matters") or "").strip()
    how_to_apply = [
        str(item).strip()
        for item in reader_guide.get("how_to_apply", [])
        if str(item).strip()
    ]
    core_concepts = [
        str(item).strip()
        for item in reader_guide.get("core_concepts", [])
        if str(item).strip()
    ]
    study_path = [
        str(item).strip()
        for item in reader_guide.get("study_path", [])
        if str(item).strip()
    ]

    lines = [markdown_content.rstrip(), "", "## 学习导读", "", "### 是什么", what_it_is or "无", "", "### 为什么重要", why_it_matters or "无", "", "### 怎么学"]
    if how_to_apply:
        lines.extend(f"- {item}" for item in how_to_apply)
    else:
        lines.append("- 无")

    lines.extend(["", "### 核心概念"])
    if core_concepts:
        lines.extend(f"- {item}" for item in core_concepts)
    else:
        lines.append("- 无")

    lines.extend(["", "### 阅读路径"])
    if study_path:
        lines.extend(f"- {item}" for item in study_path)
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def _render_summary_markdown_v2(
    *,
    title: str,
    source_type: str,
    source_value: str,
    created_at: str,
    summary: SummaryArtifact,
    tags: list[str],
    related_items: list[RelatedContextItem],
    evidence_citations: list[dict[str, object]],
    grounded_claims: list[dict[str, object]],
    summary_segments: list[dict[str, object]],
) -> str:
    lines = [
        f"# {title}",
        "",
        "## 来源信息",
        f"- 类型: {source_type}",
        f"- 来源: {source_value}",
        "",
        "## 摘要",
        summary.summary_text,
        "",
        "## 核心观点",
        summary.viewpoint_text or title,
        "",
        "## 争议点",
        summary.controversy_text or "无",
        "",
        "## 分类",
        summary.generated_category,
        "",
        "## 标签",
    ]
    if tags:
        lines.extend(f"- {tag}" for tag in tags)
    else:
        lines.append("- 无")

    lines.extend(["", "## 记忆上下文"])
    if related_items:
        for item in related_items:
            lines.append(
                f"- {item.title} ({item.final_category or 'unknown'}, score={item.score:.3f})"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## 证据引用"])
    if evidence_citations:
        for citation in evidence_citations:
            citation_id = str(citation.get("citation_id") or "cite")
            citation_title = str(citation.get("title") or citation.get("source_name") or "Untitled")
            section_title = str(citation.get("section_title") or "").strip()
            snippet = _compact_markdown_snippet(str(citation.get("snippet") or "").strip())
            headline = f"- [{citation_id}] {citation_title}"
            if section_title:
                headline += f" / {section_title}"
            lines.append(headline)
            if snippet:
                lines.append(f"  {snippet}")
    else:
        lines.append("- 无")

    lines.extend(["", "## 证据支撑结论"])
    if summary_segments:
        for segment in summary_segments:
            citation_ids = ", ".join(str(entry) for entry in segment["citation_ids"])
            lines.append(f"- {segment['text']} [{citation_ids}]")
    else:
        lines.append("- 无")

    lines.extend(["", "## 证据映射"])
    if grounded_claims:
        for claim in grounded_claims:
            citation_ids = ", ".join(str(entry) for entry in claim["citation_ids"])
            lines.append(f"- {claim['claim']} [{citation_ids}]")
    else:
        lines.append("- 无")

    lines.extend(["", "## 生成时间", created_at, ""])
    return "\n".join(lines)


def _compact_markdown_snippet(value: str, *, limit: int = 320) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


def build_summary_snapshot(
    *,
    snapshot_id: str,
    run_id: str,
    knowledge_item_id: str,
    title: str,
    source_type: str,
    source_value: str,
    created_at: str,
    summary: SummaryArtifact,
    related_items: list[RelatedContextItem],
    evidence_citations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    final_tags = _finalize_tags(summary.generated_tags, title=title, source_type=source_type)
    markdown_path = Path("summaries") / f"{snapshot_id}.md"
    serialized_memory_context_items = [
        {
            "snapshot_id": item.snapshot_id,
            "knowledge_item_id": item.knowledge_item_id,
            "title": item.title,
            "final_category": item.final_category,
            "score": round(item.score, 4),
        }
        for item in related_items
    ]
    relation_meta = build_summary_relation_meta(
        memory_context_items=serialized_memory_context_items,
        evidence_citations=evidence_citations or [],
        grounded_claims=summary.grounded_claims,
        summary_segments=summary.summary_segments,
    )

    markdown_content = _render_summary_markdown_v2(
        title=title,
        source_type=source_type,
        source_value=source_value,
        created_at=created_at,
        summary=summary,
        tags=final_tags,
        related_items=related_items,
        evidence_citations=list(relation_meta["evidence_citations"]),
        grounded_claims=list(relation_meta["grounded_claims"]),
        summary_segments=list(relation_meta["summary_segments"]),
    )
    markdown_content = _append_reader_guide_markdown(markdown_content, summary)

    return {
        "id": snapshot_id,
        "title": title,
        "knowledge_item_id": knowledge_item_id,
        "summary_run_id": run_id,
        "generated_category": summary.generated_category,
        "generated_tags": final_tags,
        "final_category": summary.generated_category,
        "final_tags": final_tags,
        "summary_text": summary.summary_text,
        "viewpoint_text": summary.viewpoint_text,
        "controversy_text": summary.controversy_text,
        "content_quality_score": summary.content_quality_score,
        "quality_meta": summary.quality_meta,
        "relation_meta": relation_meta,
        "qdrant_point_id": snapshot_id,
        "markdown_path": str(markdown_path),
        "created_at": created_at,
        "edited_at": created_at,
        "markdown_content": markdown_content,
    }


def write_summary_markdown(snapshot: dict[str, object], output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = resolve_summary_markdown_path(snapshot, output_dir)
    markdown_path.write_text(str(snapshot["markdown_content"]), encoding="utf-8")
    return str(markdown_path)


def _build_summary_markdown_filename(*, title: str, snapshot_id: str) -> str:
    return f"{safe_filename_slug(title)}-{snapshot_id}.md"


def resolve_summary_markdown_path(snapshot: dict[str, object], output_dir: Path) -> Path:
    return output_dir / _build_summary_markdown_filename(
        title=str(snapshot.get("title") or snapshot["id"]),
        snapshot_id=str(snapshot["id"]),
    )


def _finalize_tags(tags: list[str], *, title: str, source_type: str) -> list[str]:
    merged = [source_type, slugify(title)]
    merged.extend(tags)
    deduped: list[str] = []
    for tag in merged:
        normalized = tag.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped[:8]


def _render_summary_markdown(
    *,
    title: str,
    source_type: str,
    source_value: str,
    created_at: str,
    summary: SummaryArtifact,
    tags: list[str],
    related_items: list[RelatedContextItem],
    evidence_citations: list[dict[str, object]],
    grounded_claims: list[dict[str, object]],
    summary_segments: list[dict[str, object]],
) -> str:
    lines = [
        f"# {title}",
        "",
        "## 来源信息",
        f"- 类型: {source_type}",
        f"- 来源: {source_value}",
        "",
        "## 摘要",
        summary.summary_text,
        "",
        "## 核心观点",
        summary.viewpoint_text or title,
        "",
        "## 争议点",
        summary.controversy_text or "无",
        "",
        "## 分类",
        summary.generated_category,
        "",
        "## 标签",
    ]
    if tags:
        lines.extend(f"- {tag}" for tag in tags)
    else:
        lines.append("- 无")

    lines.extend(["", "## 关联内容"])
    if related_items:
        for item in related_items:
            lines.append(
                f"- {item.title} ({item.final_category or 'unknown'}, score={item.score:.3f})"
            )
    else:
        lines.append("- 暂无")

    lines.extend(["", "## Memory Context"])
    if related_items:
        for item in related_items:
            lines.append(
                f"- {item.title} ({item.final_category or 'unknown'}, score={item.score:.3f})"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Evidence Citations"])
    if evidence_citations:
        for citation in evidence_citations:
            citation_id = str(citation.get("citation_id") or "cite")
            title = str(citation.get("title") or citation.get("source_name") or "Untitled")
            section_title = str(citation.get("section_title") or "").strip()
            snippet = str(citation.get("snippet") or "").strip()
            headline = f"- [{citation_id}] {title}"
            if section_title:
                headline += f" / {section_title}"
            lines.append(headline)
            if snippet:
                lines.append(f"  {snippet}")
    else:
        lines.append("- None")

    lines.extend(["", "## Grounded Summary"])
    if summary_segments:
        for segment in summary_segments:
            citation_ids = ", ".join(str(entry) for entry in segment["citation_ids"])
            lines.append(f"- {segment['text']} [{citation_ids}]")
    else:
        lines.append("- None")

    lines.extend(["", "## Evidence Mapping"])
    if grounded_claims:
        for claim in grounded_claims:
            citation_ids = ", ".join(str(entry) for entry in claim["citation_ids"])
            lines.append(f"- {claim['claim']} [{citation_ids}]")
    else:
        lines.append("- None")

    lines.extend(["", "## 生成时间", created_at, ""])
    return "\n".join(lines)
def _render_summary_markdown_v2(
    *,
    title: str,
    source_type: str,
    source_value: str,
    created_at: str,
    summary: SummaryArtifact,
    tags: list[str],
    related_items: list[RelatedContextItem],
    evidence_citations: list[dict[str, object]],
    grounded_claims: list[dict[str, object]],
    summary_segments: list[dict[str, object]],
) -> str:
    summary_meta = summary.quality_meta or {}
    one_sentence_takeaway = str(
        summary_meta.get("one_sentence_takeaway") or summary.viewpoint_text or title
    ).strip()
    reading_focus = [
        str(item).strip()
        for item in summary_meta.get("reading_focus", [])
        if str(item).strip()
    ]
    key_points = [
        str(item).strip()
        for item in summary_meta.get("key_points", [])
        if str(item).strip()
    ]
    keywords = [
        item
        for item in summary_meta.get("keywords", [])
        if isinstance(item, dict) and str(item.get("keyword") or "").strip()
    ]
    methods_or_process = [
        str(item).strip()
        for item in summary_meta.get("methods_or_process", [])
        if str(item).strip()
    ]
    pitfalls_or_limits = [
        str(item).strip()
        for item in summary_meta.get("pitfalls_or_limits", [])
        if str(item).strip()
    ]
    code_examples = [
        item
        for item in summary_meta.get("code_examples", [])
        if isinstance(item, dict) and str(item.get("snippet") or "").strip()
    ]

    lines = [
        f"# {title}",
        "",
        "## 来源信息",
        f"- 类型: {source_type}",
        f"- 来源: {source_value}",
        "",
        "## 一句话结论",
        one_sentence_takeaway or "无",
        "",
        "## 摘要",
        summary.summary_text,
        "",
        "## 分类",
        summary.generated_category,
        "",
        "## 标签",
    ]
    if tags:
        lines.extend(f"- {tag}" for tag in tags)
    else:
        lines.append("- 无")

    lines.extend(["", "## 阅读重点"])
    if reading_focus:
        lines.extend(f"- {item}" for item in reading_focus)
    else:
        lines.append("- 无")

    lines.extend(["", "## 关键知识点"])
    if key_points:
        lines.extend(f"- {item}" for item in key_points)
    else:
        lines.append("- 无")

    lines.extend(["", "## 关键词"])
    if keywords:
        for item in keywords:
            lines.append(f"- {item['keyword']} ({float(item.get('weight', 0.0)):.2f})")
    else:
        lines.append("- 无")

    lines.extend(["", "## 方法或流程"])
    if methods_or_process:
        lines.extend(f"- {item}" for item in methods_or_process)
    else:
        lines.append("- 无")

    lines.extend(["", "## 注意点与局限"])
    if pitfalls_or_limits:
        lines.extend(f"- {item}" for item in pitfalls_or_limits)
    else:
        lines.append("- 无")

    lines.extend(["", "## 关键代码"])
    if code_examples:
        for item in code_examples:
            lines.append(
                f"- [{str(item.get('language') or 'text')}] {str(item.get('snippet') or '').strip()}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## 记忆上下文"])
    if related_items:
        for item in related_items:
            lines.append(
                f"- {item.title} ({item.final_category or 'unknown'}, score={item.score:.3f})"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## 证据引用"])
    if evidence_citations:
        for citation in evidence_citations:
            citation_id = str(citation.get("citation_id") or "cite")
            citation_title = str(citation.get("title") or citation.get("source_name") or "Untitled")
            section_title = str(citation.get("section_title") or "").strip()
            snippet = _compact_markdown_snippet(str(citation.get("snippet") or "").strip())
            headline = f"- [{citation_id}] {citation_title}"
            if section_title:
                headline += f" / {section_title}"
            lines.append(headline)
            if snippet:
                lines.append(f"  {snippet}")
    else:
        lines.append("- 无")

    lines.extend(["", "## 证据支撑摘要"])
    if summary_segments:
        for segment in summary_segments:
            citation_ids = ", ".join(str(entry) for entry in segment["citation_ids"])
            lines.append(f"- {segment['text']} [{citation_ids}]")
    else:
        lines.append("- 无")

    lines.extend(["", "## 证据映射"])
    if grounded_claims:
        for claim in grounded_claims:
            citation_ids = ", ".join(str(entry) for entry in claim["citation_ids"])
            lines.append(f"- {claim['claim']} [{citation_ids}]")
    else:
        lines.append("- 无")

    lines.extend(["", "## 生成时间", created_at, ""])
    return "\n".join(lines)
