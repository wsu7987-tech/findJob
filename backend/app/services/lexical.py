from __future__ import annotations

import re


_TOKEN_RE = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def extract_lexical_terms(text: str | None) -> list[str]:
    if not text:
        return []
    terms: list[str] = []
    for token in _TOKEN_RE.findall(text):
        normalized = token.lower()
        if _CJK_RE.search(normalized):
            terms.extend(_expand_cjk_token(normalized))
            continue
        terms.append(normalized)
    return list(dict.fromkeys(terms))


def build_lexical_document(*parts: str | None) -> str:
    terms: list[str] = []
    for part in parts:
        terms.extend(extract_lexical_terms(part))
    return " ".join(terms)


def build_fts_query(text: str) -> str | None:
    terms = extract_lexical_terms(text)
    if not terms:
        return None
    unique_terms = list(dict.fromkeys(terms))
    return " OR ".join(f'"{term}"' for term in unique_terms)


def _expand_cjk_token(token: str) -> list[str]:
    if len(token) <= 2:
        return [token]

    max_ngram = min(len(token), 6)
    expanded = [token]
    for size in range(2, max_ngram + 1):
        for index in range(0, len(token) - size + 1):
            expanded.append(token[index : index + size])
    return list(dict.fromkeys(expanded))
