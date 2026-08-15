from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def new_id() -> str:
    return str(uuid4())


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def safe_filename_slug(value: str) -> str:
    normalized = re.sub(r"\s+", "-", value.strip())
    normalized = re.sub(r"[<>:\"/\\\\|?*\x00-\x1f]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    if not normalized:
        return "item"

    chars: list[str] = []
    for char in normalized:
        if char.isascii():
            if char.isalnum():
                chars.append(char.lower())
            elif char in {"-", "_"}:
                chars.append("-")
        elif "\u4e00" <= char <= "\u9fff":
            chars.append(char)
        else:
            chars.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(chars)).strip("-")
    return slug or "item"
