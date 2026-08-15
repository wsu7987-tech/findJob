from __future__ import annotations

from pathlib import Path

from backend.app.services.ai import RelatedContextItem, SummaryArtifact
from backend.app.utils import slugify


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
) -> dict[str, object]:
    final_tags = _finalize_tags(summary.generated_tags, title=title, source_type=source_type)
    markdown_path = Path("summaries") / f"{snapshot_id}.md"
    relation_meta = {
        "related_items": [
            {
                "snapshot_id": item.snapshot_id,
                "knowledge_item_id": item.knowledge_item_id,
                "title": item.title,
                "final_category": item.final_category,
                "score": round(item.score, 4),
            }
            for item in related_items
        ]
    }

    return {
        "id": snapshot_id,
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
        "markdown_content": _render_summary_markdown(
            title=title,
            source_type=source_type,
            source_value=source_value,
            created_at=created_at,
            summary=summary,
            tags=final_tags,
            related_items=related_items,
        ),
    }


def write_summary_markdown(snapshot: dict[str, object], output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f"{snapshot['id']}.md"
    markdown_path.write_text(str(snapshot["markdown_content"]), encoding="utf-8")
    return str(markdown_path)


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
) -> str:
    lines = [
        f"# {title}",
        "",
        "## Source",
        f"- Type: {source_type}",
        f"- Value: {source_value}",
        "",
        "## Summary",
        summary.summary_text,
        "",
        "## Viewpoint",
        summary.viewpoint_text or "No specific viewpoint extracted.",
        "",
        "## Controversy",
        summary.controversy_text or "No explicit controversy extracted.",
        "",
        "## Classification",
        f"- Category: {summary.generated_category}",
        f"- Quality Score: {summary.content_quality_score}",
        "",
        "## Tags",
    ]
    if tags:
        lines.extend(f"- {tag}" for tag in tags)
    else:
        lines.append("- none")

    lines.extend(["", "## Related Items"])
    if related_items:
        for item in related_items:
            lines.append(
                f"- {item.title} ({item.final_category or 'unknown'}, score={item.score:.3f})"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Generated At", created_at, ""])
    return "\n".join(lines)
