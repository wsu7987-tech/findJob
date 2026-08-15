from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.graphs.report_graph import build_report_graph
from backend.app.services.evidence_bundle import build_evidence_bundle


def build_report_precheck(db: Database, config: AppConfig) -> dict[str, object]:
    candidate_week_keys = sorted(
        {current_week_key(), *_list_existing_week_keys(db), *_list_snapshot_week_keys(db)},
        reverse=True,
    )
    selected_week_key = candidate_week_keys[0]
    existing_versions = _list_existing_versions(db, selected_week_key)

    return {
        "week_key": selected_week_key,
        "available_week_keys": candidate_week_keys,
        "existing_versions": existing_versions,
        "next_version": (max(existing_versions) + 1) if existing_versions else 1,
    }


def create_report_run(
    db: Database,
    config: AppConfig,
    payload,
) -> dict[str, object]:
    graph = build_report_graph(db)
    final_state = graph.invoke(
        {
            "week_key": payload.week_key or current_week_key(),
            "config_snapshot": serialize_report_config_snapshot(config),
        }
    )
    return {
        "run_id": str(final_state["run_id"]),
        "week_key": str(final_state["week_key"]),
        "version": int(final_state["version"]),
    }


def list_report_versions(db: Database, week_key: str) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT week_key, version, generated_at
            FROM weekly_report_versions
            WHERE week_key = ?
            ORDER BY version DESC
            """,
            (week_key,),
        ).fetchall()

    return [
        {
            "week_key": row["week_key"],
            "version": row["version"],
            "generated_at": row["generated_at"],
        }
        for row in rows
    ]


def get_report_version(db: Database, week_key: str, version: int) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT
              id,
              week_key,
              version,
              markdown_content,
              snapshot_payload,
              markdown_path,
              generated_at
            FROM weekly_report_versions
            WHERE week_key = ? AND version = ?
            """,
            (week_key, version),
        ).fetchone()

    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Report version not found.",
        )

    snapshot_payload = _normalize_stored_snapshot_payload(json.loads(row["snapshot_payload"]))

    return {
        "id": row["id"],
        "week_key": row["week_key"],
        "version": row["version"],
        "markdown_content": row["markdown_content"],
        "snapshot_payload": snapshot_payload,
        "markdown_path": row["markdown_path"],
        "generated_at": row["generated_at"],
    }


def current_week_key(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = current.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def week_key_bounds(week_key: str) -> tuple[datetime, datetime]:
    try:
        year_text, week_text = week_key.split("-W", maxsplit=1)
        iso_year = int(year_text)
        iso_week = int(week_text)
        start = datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as exc:
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Invalid week_key.",
        ) from exc
    return start, start + timedelta(days=7)


def serialize_report_config_snapshot(config: AppConfig) -> dict[str, object]:
    return {
        "report_output_dir": str(config.report_output_dir),
    }


def _list_existing_week_keys(db: Database) -> list[str]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT week_key
            FROM weekly_report_versions
            ORDER BY week_key DESC
            """
        ).fetchall()
    return [str(row["week_key"]) for row in rows]


def _list_snapshot_week_keys(db: Database) -> list[str]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT created_at
            FROM item_result_snapshots
            WHERE created_at IS NOT NULL AND created_at != ''
            ORDER BY created_at DESC
            """
        ).fetchall()
    week_keys: list[str] = []
    for row in rows:
        week_key = _week_key_from_timestamp(str(row["created_at"]))
        if week_key and week_key not in week_keys:
            week_keys.append(week_key)
    return week_keys


def _week_key_from_timestamp(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return current_week_key(parsed.astimezone(timezone.utc))


def _list_existing_versions(db: Database, week_key: str) -> list[int]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT version
            FROM weekly_report_versions
            WHERE week_key = ?
            ORDER BY version ASC
            """,
            (week_key,),
        ).fetchall()
    return [int(row["version"]) for row in rows]


def _load_report_snapshot_rows(db: Database, week_key: str):
    week_start, week_end = week_key_bounds(week_key)
    with db.connect() as connection:
        return connection.execute(
            """
            SELECT
              s.id,
              s.generated_category,
              s.final_category,
              s.summary_text,
              s.relation_meta,
              s.created_at,
              ki.source_type,
              ki.title
            FROM item_result_snapshots AS s
            JOIN knowledge_items AS ki ON ki.id = s.knowledge_item_id
            WHERE julianday(s.created_at) >= julianday(?)
              AND julianday(s.created_at) < julianday(?)
            ORDER BY s.created_at DESC, s.id DESC
            """
            ,
            (
                week_start.isoformat().replace("+00:00", "Z"),
                week_end.isoformat().replace("+00:00", "Z"),
            ),
        ).fetchall()


def _build_snapshot_payload(snapshot_rows) -> dict[str, object]:
    category_stats = Counter()
    source_distribution = Counter()
    reading_trend = Counter()
    evidence_citation_total = 0
    grounded_items = _build_grounded_items(snapshot_rows)

    for row in snapshot_rows:
        category_stats[row["final_category"] or row["generated_category"] or "uncategorized"] += 1
        source_distribution[row["source_type"] or "unknown"] += 1
        reading_trend[str(row["created_at"])[:10]] += 1
        evidence_citation_total += _count_evidence_citations(row["relation_meta"])

    return {
        "category_stats": dict(category_stats),
        "source_distribution": dict(source_distribution),
        "reading_trend": dict(sorted(reading_trend.items())),
        "evidence_citation_total": evidence_citation_total,
        "grounded_claim_total": len(grounded_items),
        "grounded_items": grounded_items,
        "items": _build_snapshot_items(snapshot_rows),
    }


def _normalize_stored_snapshot_payload(snapshot_payload: object) -> dict[str, object]:
    if not isinstance(snapshot_payload, dict):
        return {
            "category_stats": {},
            "source_distribution": {},
            "reading_trend": {},
            "evidence_citation_total": 0,
            "grounded_claim_total": 0,
            "grounded_items": [],
            "items": [],
        }

    items = [
        _normalize_stored_snapshot_item(item)
        for item in snapshot_payload.get("items", [])
        if isinstance(item, dict)
    ]
    grounded_items = [
        _normalize_stored_grounded_item(item)
        for item in snapshot_payload.get("grounded_items", [])
        if isinstance(item, dict)
    ]
    grounded_items = [item for item in grounded_items if item is not None]

    return {
        "category_stats": dict(snapshot_payload.get("category_stats") or {}),
        "source_distribution": dict(snapshot_payload.get("source_distribution") or {}),
        "reading_trend": dict(snapshot_payload.get("reading_trend") or {}),
        "evidence_citation_total": int(
            snapshot_payload.get("evidence_citation_total")
            or sum(int(item["evidence_citation_count"]) for item in items)
        ),
        "grounded_claim_total": int(
            snapshot_payload.get("grounded_claim_total") or len(grounded_items)
        ),
        "grounded_items": grounded_items,
        "items": items,
    }


def _normalize_stored_snapshot_item(item: dict[str, object]) -> dict[str, object]:
    evidence_bundle = item.get("evidence_bundle")
    if isinstance(evidence_bundle, dict):
        normalized_bundle = _normalize_stored_evidence_bundle(evidence_bundle)
    else:
        normalized_bundle = {
            "memory_context_items": [],
            "citations": [],
            "grounded_claims": [],
            "summary_segments": [],
            "memory_context_count": int(item.get("memory_context_count") or 0),
            "evidence_citation_count": int(item.get("evidence_citation_count") or 0),
            "grounded_claim_count": int(item.get("grounded_claim_count") or 0),
        }
    return {
        "snapshot_id": str(item.get("snapshot_id") or ""),
        "title": str(item.get("title") or ""),
        "final_category": str(item.get("final_category") or ""),
        "created_at": str(item.get("created_at") or ""),
        "evidence_citation_count": int(
            item.get("evidence_citation_count") or normalized_bundle["evidence_citation_count"]
        ),
        "memory_context_count": int(
            item.get("memory_context_count") or normalized_bundle["memory_context_count"]
        ),
        "grounded_claim_count": int(
            item.get("grounded_claim_count") or normalized_bundle["grounded_claim_count"]
        ),
        "top_evidence_titles": list(item.get("top_evidence_titles") or _extract_top_evidence_titles(normalized_bundle)),
        "top_grounded_claims": list(item.get("top_grounded_claims") or _extract_top_grounded_claims(normalized_bundle)),
        "evidence_bundle": normalized_bundle,
    }


def _normalize_stored_evidence_bundle(evidence_bundle: dict[str, object]) -> dict[str, object]:
    citations = evidence_bundle.get("citations")
    grounded_claims = evidence_bundle.get("grounded_claims")
    summary_segments = evidence_bundle.get("summary_segments")
    memory_context_items = evidence_bundle.get("memory_context_items")
    if all(
        isinstance(value, list)
        for value in (citations, grounded_claims, summary_segments, memory_context_items)
    ):
        return {
            "memory_context_items": list(memory_context_items),
            "citations": list(citations),
            "grounded_claims": list(grounded_claims),
            "summary_segments": list(summary_segments),
            "memory_context_count": int(
                evidence_bundle.get("memory_context_count") or len(memory_context_items)
            ),
            "evidence_citation_count": int(
                evidence_bundle.get("evidence_citation_count") or len(citations)
            ),
            "grounded_claim_count": int(
                evidence_bundle.get("grounded_claim_count") or len(grounded_claims)
            ),
        }
    return build_evidence_bundle(json.dumps(evidence_bundle, ensure_ascii=False))


def _normalize_stored_grounded_item(item: dict[str, object]) -> dict[str, object] | None:
    claim = str(item.get("claim") or "").strip()
    if not claim:
        return None
    return {
        "snapshot_id": str(item.get("snapshot_id") or ""),
        "title": str(item.get("title") or ""),
        "final_category": str(item.get("final_category") or ""),
        "claim": claim,
        "citation_ids": [str(entry) for entry in item.get("citation_ids", []) if str(entry).strip()],
        "evidence_titles": [str(entry) for entry in item.get("evidence_titles", []) if str(entry).strip()],
    }


def _build_snapshot_items(snapshot_rows) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for row in snapshot_rows:
        evidence_bundle = build_evidence_bundle(row["relation_meta"])
        items.append(
            {
                "snapshot_id": row["id"],
                "title": row["title"] or row["id"],
                "final_category": row["final_category"] or row["generated_category"],
                "created_at": row["created_at"],
                "evidence_citation_count": evidence_bundle["evidence_citation_count"],
                "memory_context_count": evidence_bundle["memory_context_count"],
                "grounded_claim_count": evidence_bundle["grounded_claim_count"],
                "top_evidence_titles": _extract_top_evidence_titles(evidence_bundle),
                "top_grounded_claims": _extract_top_grounded_claims(evidence_bundle),
                "evidence_bundle": evidence_bundle,
            }
        )
    return items


def _build_grounded_items(snapshot_rows) -> list[dict[str, object]]:
    grounded_items: list[dict[str, object]] = []
    for row in snapshot_rows:
        title = row["title"] or row["id"]
        final_category = row["final_category"] or row["generated_category"]
        for grounded_claim in _extract_grounded_claim_entries(row["relation_meta"]):
            grounded_items.append(
                {
                    "snapshot_id": row["id"],
                    "title": title,
                    "final_category": final_category,
                    "claim": grounded_claim["claim"],
                    "citation_ids": grounded_claim["citation_ids"],
                    "evidence_titles": grounded_claim["evidence_titles"],
                }
            )
    return grounded_items


def _count_evidence_citations(relation_meta: object) -> int:
    return int(build_evidence_bundle(relation_meta)["evidence_citation_count"])


def _count_memory_context_items(relation_meta: object) -> int:
    return int(build_evidence_bundle(relation_meta)["memory_context_count"])


def _extract_top_evidence_titles(relation_meta_or_bundle: object) -> list[str]:
    if isinstance(relation_meta_or_bundle, dict) and "citations" in relation_meta_or_bundle:
        citations = relation_meta_or_bundle.get("citations")
    else:
        citations = build_evidence_bundle(relation_meta_or_bundle)["citations"]
    titles: list[str] = []
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict):
            continue
        title = str(citation.get("title") or citation.get("source_name") or "").strip()
        if title and title not in titles:
            titles.append(title)
    return titles[:3]


def _count_grounded_claims(relation_meta: object) -> int:
    return int(build_evidence_bundle(relation_meta)["grounded_claim_count"])


def _extract_top_grounded_claims(relation_meta_or_bundle: object) -> list[str]:
    return [
        str(entry["claim"])
        for entry in _extract_grounded_claim_entries(relation_meta_or_bundle)[:3]
    ]


def _extract_grounded_claim_entries(relation_meta_or_bundle: object) -> list[dict[str, object]]:
    if isinstance(relation_meta_or_bundle, dict) and "grounded_claims" in relation_meta_or_bundle:
        grounded_claims = relation_meta_or_bundle.get("grounded_claims")
    else:
        grounded_claims = build_evidence_bundle(relation_meta_or_bundle)["grounded_claims"]
    if not isinstance(grounded_claims, list):
        return []
    return [entry for entry in grounded_claims if isinstance(entry, dict)]


def _build_markdown_content(
    *,
    week_key: str,
    version: int,
    snapshot_rows,
    snapshot_payload: dict[str, object],
) -> str:
    items = snapshot_payload.get("items", [])
    item_entries = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    grounded_items = snapshot_payload.get("grounded_items", [])
    grounded_entries = [
        item for item in grounded_items if isinstance(item, dict)
    ] if isinstance(grounded_items, list) else []
    evidence_total = int(snapshot_payload.get("evidence_citation_total") or 0)
    grounded_total = int(snapshot_payload.get("grounded_claim_total") or len(grounded_entries))

    lines = [
        f"# 周报 {week_key}",
        "",
        f"- 版本：第 {version} 版",
        f"- 本周结果条目：{len(snapshot_rows)} 条",
        f"- 原文证据：{evidence_total} 条",
        f"- 可引用结论：{grounded_total} 条",
        "",
        "## 本周概览",
        f"- 本周共沉淀 {len(snapshot_rows)} 条总结结果，覆盖 {len(snapshot_payload.get('category_stats', {}) or {})} 个分类。",
        f"- 证据链累计包含 {evidence_total} 条原文证据，提炼出 {grounded_total} 条可引用结论。",
        "- 建议优先复核证据较少、分类为待确认或结论表述较宽泛的条目。",
        "",
        "## 分类分布",
    ]

    category_stats = snapshot_payload.get("category_stats", {})
    if isinstance(category_stats, dict) and category_stats:
        lines.extend(f"- {name}：{value} 条" for name, value in category_stats.items())
    else:
        lines.append("- 暂无总结结果。")

    lines.extend(["", "## 来源分布"])
    source_distribution = snapshot_payload.get("source_distribution", {})
    if isinstance(source_distribution, dict) and source_distribution:
        lines.extend(
            f"- {_source_type_label(str(name))}：{value} 条"
            for name, value in source_distribution.items()
        )
    else:
        lines.append("- 暂无来源数据。")

    lines.extend(["", "## 证据充分的条目"])
    if item_entries:
        evidence_ranked_items = sorted(
            item_entries,
            key=lambda item: (
                -int(item.get("evidence_citation_count") or 0),
                str(item.get("title") or ""),
            ),
        )
        for item in evidence_ranked_items:
            title = str(item.get("title") or item.get("snapshot_id") or "untitled")
            evidence_count = int(item.get("evidence_citation_count") or 0)
            memory_count = int(item.get("memory_context_count") or 0)
            grounded_count = int(item.get("grounded_claim_count") or 0)
            top_titles = item.get("top_evidence_titles")
            suffix = ""
            if isinstance(top_titles, list) and top_titles:
                suffix = f"；主要证据：{'、'.join(str(entry) for entry in top_titles)}"
            lines.append(
                f"- {title}：原文证据 {evidence_count} 条，辅助摘要 {memory_count} 条，可引用结论 {grounded_count} 条{suffix}"
            )
    else:
        lines.append("- 暂无总结结果。")

    lines.extend(["", "## 可引用结论"])
    if grounded_entries:
        claim_ranked_items = sorted(
            grounded_entries,
            key=lambda item: (
                str(item.get("title") or ""),
                str(item.get("title") or ""),
            ),
        )
        for item in claim_ranked_items:
            title = str(item.get("title") or item.get("snapshot_id") or "untitled")
            claim_text = str(item.get("claim") or "").strip()
            evidence_titles = item.get("evidence_titles")
            suffix = ""
            if isinstance(evidence_titles, list) and evidence_titles:
                suffix = f"（依据：{'、'.join(str(entry) for entry in evidence_titles)}）"
            lines.append(f"- {title}：{claim_text}{suffix}")
    else:
        lines.append("- 暂无可引用结论。")

    lines.extend(["", "## 明细条目"])
    if snapshot_rows:
        for row in snapshot_rows:
            title = row["title"] or row["id"]
            grounded_claims = _extract_top_grounded_claims(row["relation_meta"])
            summary_text = grounded_claims[0] if grounded_claims else (row["summary_text"] or "")
            lines.append(
                f"- {title}：{summary_text[:120]}"
                + ("..." if len(summary_text) > 120 else "")
            )
    else:
        lines.append("- 暂无总结结果。")

    lines.extend(["", "## 下周建议"])
    if item_entries:
        low_evidence_items = [
            str(item.get("title") or item.get("snapshot_id") or "未命名条目")
            for item in item_entries
            if int(item.get("evidence_citation_count") or 0) < 2
        ]
        if low_evidence_items:
            lines.append(f"- 复核证据较少的条目：{'、'.join(low_evidence_items[:5])}。")
        else:
            lines.append("- 本周条目的证据覆盖较稳定，可优先推进主题归档和对外复用。")
        lines.append("- 从可引用结论中挑选高价值内容，沉淀为问答中心的常用检索入口。")
    else:
        lines.append("- 本周暂无可分析条目，建议先完成总结池处理后再生成周报。")

    return "\n".join(lines) + "\n"


def _source_type_label(value: str) -> str:
    labels = {
        "url": "网页链接",
        "pdf": "PDF 文件",
        "markdown": "Markdown 文档",
        "text": "纯文本",
    }
    return labels.get(value, value or "未知来源")


def _write_report_markdown(
    *,
    output_dir: Path,
    week_key: str,
    version: int,
    markdown_content: str,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f"{week_key}-v{version}.md"
    markdown_path.write_text(markdown_content, encoding="utf-8")
    return str(markdown_path)
