from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.pdf_parse.quality import evaluate_parse_quality
from backend.app.services.pdf_parse.service import build_default_pdf_parse_service
from backend.app.services.fine_job.resume_text import clean_resume_text
from backend.app.utils import new_id, utc_now


SUPPORTED_EXTENSIONS = {".pdf"}

_SECTION_ALIASES = (
    ("education", ("教育经历", "教育背景", "学历背景", "education", "education background")),
    (
        "experience",
        ("工作经历", "工作经验", "职业经历", "实习经历", "experience", "work experience", "employment"),
    ),
    ("project", ("项目经历", "项目经验", "项目", "projects", "project experience")),
    ("skill", ("专业技能", "技能", "技术栈", "skills", "technical skills")),
)
_SECTION_ALIAS_MAP = {
    alias.casefold(): section
    for section, aliases in _SECTION_ALIASES
    for alias in aliases
}
_GENERIC_RESUME_TITLES = {"简历", "个人简历", "resume", "curriculum vitae", "cv"}


def list_resumes(db: Database) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, name, file_path, parser_name, preview_text, page_count,
                   char_count, quality_score, is_ocr, warnings_json, status,
                   created_at, updated_at
            FROM fj_resumes
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    return [_serialize_summary(row) for row in rows]


def get_resume(db: Database, resume_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, name, file_path, file_hash, parser_name, raw_text,
                   markdown_text, preview_text, page_count, char_count,
                   quality_score, is_ocr, warnings_json, fallback_from,
                   fallback_reason, status, created_at, updated_at
            FROM fj_resumes
            WHERE id = ?
            """,
            (resume_id,),
        ).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="FineJob resume not found.",
        )
    return _serialize_detail(row)


def create_resume_from_file(
    *,
    db: Database,
    config: AppConfig,
    file_path: str,
    name: str | None,
    parser_name: str,
) -> dict[str, object]:
    path = Path(file_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Resume file does not exist.",
        )
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Only PDF resumes are supported in the first version.",
        )

    file_hash = _hash_file(path)
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM fj_resumes WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()
    if existing is not None:
        return get_resume(db, str(existing["id"]))

    parse_service = build_default_pdf_parse_service(config)
    parsed = parse_service.parse_file(file_path=path, parser_name=parser_name)
    cleaned_raw_text = clean_resume_text(parsed.raw_text)
    quality = evaluate_parse_quality(
        parser_name=parsed.parser_name,
        raw_text=cleaned_raw_text,
        markdown_text=parsed.markdown_text,
        page_count=parsed.page_count,
    )
    now = utc_now()
    resume_id = new_id()
    resume_name = name.strip() if name and name.strip() else path.stem

    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_resumes (
              id, name, file_path, file_hash, parser_name, raw_text,
              markdown_text, preview_text, page_count, char_count,
              quality_score, is_ocr, warnings_json, fallback_from,
              fallback_reason, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resume_id,
                resume_name,
                str(path),
                file_hash,
                parsed.parser_name,
                cleaned_raw_text,
                parsed.markdown_text,
                cleaned_raw_text,
                parsed.page_count,
                len(cleaned_raw_text),
                quality.score,
                1 if parsed.is_ocr else 0,
                json.dumps([*parsed.warnings, *quality.warnings], ensure_ascii=False),
                parsed.fallback_from,
                parsed.fallback_reason or quality.fallback_reason,
                "parsed" if quality.score > 0 else "failed",
                now,
                now,
            ),
        )
    return get_resume(db, resume_id)


def delete_resume(db: Database, resume_id: str) -> None:
    with db.connect() as connection:
        cursor = connection.execute("DELETE FROM fj_resumes WHERE id = ?", (resume_id,))
        if cursor.rowcount == 0:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="FineJob resume not found.",
            )
        # 数据库外键会级联清理简历事实，并将其他业务表中的简历引用置空。


def list_resume_facts(db: Database, resume_id: str) -> list[dict[str, object]]:
    _ensure_resume_exists(db, resume_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, resume_id, fact_type, fact_key, fact_value, confidence,
                   source_text, user_confirmed, sensitive, created_at, updated_at
            FROM fj_resume_facts
            WHERE resume_id = ?
            ORDER BY
              CASE fact_type
                WHEN 'basic' THEN 1
                WHEN 'contact' THEN 2
                WHEN 'education' THEN 3
                WHEN 'experience' THEN 4
                WHEN 'project' THEN 5
                WHEN 'skill' THEN 6
                ELSE 99
              END,
              created_at,
              id
            """,
            (resume_id,),
        ).fetchall()
    return [_serialize_fact(row) for row in rows]


def extract_resume_facts(db: Database, resume_id: str) -> list[dict[str, object]]:
    resume = get_resume(db, resume_id)
    text = str(resume.get("raw_text") or resume.get("preview_text") or "")
    existing = list_resume_facts(db, resume_id)
    if existing and any(bool(fact.get("user_confirmed")) for fact in existing):
        return existing

    facts = _extract_facts_by_rules(resume_id, text)
    return save_resume_facts(db, resume_id, facts)


def save_resume_facts(
    db: Database,
    resume_id: str,
    facts: list[dict[str, object]],
) -> list[dict[str, object]]:
    _ensure_resume_exists(db, resume_id)
    now = utc_now()
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_resume_facts WHERE resume_id = ?", (resume_id,))
        for fact in facts:
            fact_value = str(fact.get("fact_value") or "").strip()
            fact_key = str(fact.get("fact_key") or "").strip()
            if not fact_key or not fact_value:
                continue
            fact_type = str(fact.get("fact_type") or "basic").strip()
            connection.execute(
                """
                INSERT INTO fj_resume_facts (
                  id, resume_id, fact_type, fact_key, fact_value, confidence,
                  source_text, user_confirmed, sensitive, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(fact.get("id") or new_id()),
                    resume_id,
                    fact_type,
                    fact_key,
                    fact_value,
                    float(fact.get("confidence") or 1),
                    _optional_str(fact.get("source_text")),
                    1 if bool(fact.get("user_confirmed", True)) else 0,
                    1 if bool(fact.get("sensitive", False)) else 0,
                    now,
                    now,
                ),
            )
        # 整组事实替换后递增简历事实版本，旧评估和旧预览会据此失效。
        connection.execute(
            "UPDATE fj_resumes SET facts_version = facts_version + 1, updated_at = ? WHERE id = ?",
            (now, resume_id),
        )
    return list_resume_facts(db, resume_id)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_resume_exists(db: Database, resume_id: str) -> None:
    with db.connect() as connection:
        row = connection.execute("SELECT id FROM fj_resumes WHERE id = ?", (resume_id,)).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="FineJob resume not found.",
        )


def _extract_facts_by_rules(resume_id: str, text: str) -> list[dict[str, object]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    facts: list[dict[str, object]] = []
    email = _first_match(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
    phone = _first_match(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)", text)
    if lines:
        first_line = lines[0]
        first_line_key = re.sub(r"[\s:|_-]+", " ", first_line).strip().casefold()
        if len(first_line) <= 40 and first_line_key not in _GENERIC_RESUME_TITLES:
            facts.append(_new_fact(resume_id, "basic", "可能姓名/标题", first_line, 0.35, first_line))
    if phone:
        facts.append(_new_fact(resume_id, "contact", "手机号", phone, 0.88, phone, sensitive=True))
    if email:
        facts.append(_new_fact(resume_id, "contact", "邮箱", email, 0.9, email, sensitive=True))

    sections = _collect_sections(lines)
    for fact_type in ("education", "experience", "project", "skill"):
        value = sections.get(fact_type)
        if value:
            facts.append(_new_fact(resume_id, fact_type, _section_label(fact_type), value, 0.55, value))

    if not facts and text.strip():
        full_text = text.strip()
        facts.append(_new_fact(resume_id, "basic", "简历全文", full_text, 0.2, full_text))
    return facts


def _collect_sections(lines: list[str]) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        heading, inline_content = _match_section_heading(line)
        if heading:
            current = heading
            sections.setdefault(current, [])
            if inline_content:
                sections[current].append(inline_content)
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items() if value}


def _match_section_heading(line: str) -> tuple[str | None, str | None]:
    parts = re.split(r"\s*[:|]\s*", line, maxsplit=1)
    heading_text = parts[0].strip()
    normalized_heading = re.sub(r"\s+", " ", heading_text).casefold()
    section = _SECTION_ALIAS_MAP.get(normalized_heading)
    if section:
        inline_content = parts[1].strip() if len(parts) == 2 else None
        return section, inline_content or None
    return None, None


def _section_label(section: str) -> str:
    return {
        "education": "教育经历",
        "experience": "工作经历",
        "project": "项目经历",
        "skill": "专业技能",
    }.get(section, section)


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(0) if match else None


def _new_fact(
    resume_id: str,
    fact_type: str,
    fact_key: str,
    fact_value: str,
    confidence: float,
    source_text: str | None,
    *,
    sensitive: bool = False,
) -> dict[str, object]:
    return {
        "id": new_id(),
        "resume_id": resume_id,
        "fact_type": fact_type,
        "fact_key": fact_key,
        "fact_value": fact_value,
        "confidence": confidence,
        "source_text": source_text,
        "user_confirmed": False,
        "sensitive": sensitive,
    }


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_warnings(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _serialize_summary(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "file_path": row["file_path"],
        "parser_name": row["parser_name"],
        "preview_text": row["preview_text"],
        "page_count": row["page_count"],
        "char_count": row["char_count"],
        "quality_score": row["quality_score"],
        "is_ocr": bool(row["is_ocr"]),
        "warnings": _load_warnings(row["warnings_json"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _serialize_detail(row) -> dict[str, object]:
    return {
        **_serialize_summary(row),
        "file_hash": row["file_hash"],
        "raw_text": row["raw_text"],
        "markdown_text": row["markdown_text"],
        "fallback_from": row["fallback_from"],
        "fallback_reason": row["fallback_reason"],
    }


def _serialize_fact(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "resume_id": row["resume_id"],
        "fact_type": row["fact_type"],
        "fact_key": row["fact_key"],
        "fact_value": row["fact_value"],
        "confidence": row["confidence"],
        "source_text": row["source_text"],
        "user_confirmed": bool(row["user_confirmed"]),
        "sensitive": bool(row["sensitive"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
