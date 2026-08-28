from __future__ import annotations

import re
import unicodedata


_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_MULTISPACE_RE = re.compile(r"[ \t]+")
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9](?:\s*\d){9})(?!\d)")
_EMAIL_SEPARATOR_RE = re.compile(r"(?<=[A-Za-z0-9])\s*([@.])\s*(?=[A-Za-z0-9])")
_BULLET_RE = re.compile(r"^(?:[-*+]|[•●▪◦‣∙])\s+")
_PAGE_MARKER_RE = re.compile(
    r"^(?:"
    r"page\s*\d+(?:\s*(?:of|/)\s*\d+)?|"
    r"第\s*\d+\s*页(?:\s*/\s*\d+)?"
    r")$",
    re.IGNORECASE,
)
_DECORATIVE_LINE_RE = re.compile(r"^[-|｜•·●○◦▪‣∙—–_*=~.\s]{3,}$")


def clean_resume_text(text: str | None) -> str:
    """清洗简历解析文本，同时保留段落和列表结构。"""
    if not text:
        return ""

    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = normalized.replace("\u00a0", " ").replace("\u3000", " ")
    # 英文单词跨页或跨行时，移除断词连字符，避免技能名称被拆开。
    normalized = re.sub(r"(?<=[A-Za-z])-\n(?=[A-Za-z])", "", normalized)

    cleaned_lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = _clean_resume_line(raw_line)
        if not line or _is_noise_line(line):
            if not line and cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _clean_resume_line(line: str) -> str:
    line = _MULTISPACE_RE.sub(" ", line).strip()
    line = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", line)
    line = re.sub(r"\s+([，。；：、！？）】》])", r"\1", line)
    line = re.sub(r"([（【《])\s+", r"\1", line)
    if "@" in line:
        line = _EMAIL_SEPARATOR_RE.sub(r"\1", line)
    line = _PHONE_RE.sub(lambda match: match.group(1).replace(" ", ""), line)
    line = _BULLET_RE.sub("• ", line)
    return line


def _is_noise_line(line: str) -> bool:
    return bool(_PAGE_MARKER_RE.fullmatch(line) or _DECORATIVE_LINE_RE.fullmatch(line))
