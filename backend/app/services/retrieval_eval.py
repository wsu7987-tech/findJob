from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.app.services.retrieval_types import RetrievalFilters, RetrievalResult


@dataclass(slots=True)
class RetrievalEvalCase:
    case_id: str
    query: str
    filters: RetrievalFilters
    expected_knowledge_item_ids: list[str]
    forbidden_knowledge_item_ids: list[str]


@dataclass(slots=True)
class RetrievalEvalDataset:
    dataset_id: str | None
    dataset_version: str | None
    owner: str | None
    notes: str | None
    cases: list[RetrievalEvalCase]


@dataclass(slots=True)
class RetrievalEvalCaseReport:
    case_id: str
    query: str
    returned_knowledge_item_ids: list[str]
    missing_expected_knowledge_item_ids: list[str]
    unexpected_knowledge_item_ids: list[str]
    passed: bool


@dataclass(slots=True)
class RetrievalEvalReport:
    total_cases: int
    passed_cases: int
    failed_cases: int
    case_reports: list[RetrievalEvalCaseReport]


@dataclass(slots=True)
class EvalBaselineComparison:
    baseline_passed_cases: int
    current_passed_cases: int
    regressed_case_ids: list[str]
    improved_case_ids: list[str]
    unchanged_case_ids: list[str]


def load_retrieval_eval_dataset(path: str | Path) -> RetrievalEvalDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases_payload: list[dict[str, object]]
    dataset_id: str | None = None
    dataset_version: str | None = None
    owner: str | None = None
    notes: str | None = None
    if isinstance(payload, list):
        cases_payload = payload
    elif isinstance(payload, dict):
        cases_payload = list(payload.get("cases") or [])
        dataset_id = _normalize_optional_string(payload.get("dataset_id"))
        dataset_version = _normalize_optional_string(payload.get("dataset_version"))
        owner = _normalize_optional_string(payload.get("owner"))
        notes = _normalize_optional_string(payload.get("notes"))
    else:
        raise ValueError("Retrieval eval payload must be a JSON array or an object with a cases field.")

    dataset = RetrievalEvalDataset(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        owner=owner,
        notes=notes,
        cases=[_load_retrieval_eval_case(item) for item in cases_payload],
    )
    _validate_retrieval_eval_dataset(dataset)
    return dataset


def load_retrieval_eval_cases(path: str | Path) -> list[RetrievalEvalCase]:
    return load_retrieval_eval_dataset(path).cases


def evaluate_retrieval(
    *,
    cases: list[RetrievalEvalCase],
    search_case: Callable[[RetrievalEvalCase], RetrievalResult],
) -> RetrievalEvalReport:
    case_reports: list[RetrievalEvalCaseReport] = []
    for case in cases:
        result = search_case(case)
        returned_ids = _dedupe_preserve_order(
            [hit.knowledge_item_id for hit in result.child_hits if hit.knowledge_item_id]
        )
        missing_expected = [
            knowledge_item_id
            for knowledge_item_id in case.expected_knowledge_item_ids
            if knowledge_item_id not in returned_ids
        ]
        unexpected = [
            knowledge_item_id
            for knowledge_item_id in case.forbidden_knowledge_item_ids
            if knowledge_item_id in returned_ids
        ]
        case_reports.append(
            RetrievalEvalCaseReport(
                case_id=case.case_id,
                query=case.query,
                returned_knowledge_item_ids=returned_ids,
                missing_expected_knowledge_item_ids=missing_expected,
                unexpected_knowledge_item_ids=unexpected,
                passed=not missing_expected and not unexpected,
            )
        )

    passed_cases = sum(1 for item in case_reports if item.passed)
    total_cases = len(case_reports)
    return RetrievalEvalReport(
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=total_cases - passed_cases,
        case_reports=case_reports,
    )


def compare_eval_report_to_baseline(
    *,
    current_report_payload: dict[str, object],
    baseline_payload: dict[str, object],
) -> EvalBaselineComparison:
    current_cases = {
        str(item["case_id"]): bool(item.get("passed"))
        for item in list(current_report_payload.get("case_reports") or [])
    }
    baseline_cases = {
        str(item["case_id"]): bool(item.get("passed"))
        for item in list(baseline_payload.get("case_reports") or [])
    }
    case_ids = sorted(set(current_cases) | set(baseline_cases))

    regressed: list[str] = []
    improved: list[str] = []
    unchanged: list[str] = []
    for case_id in case_ids:
        current_passed = current_cases.get(case_id)
        baseline_passed = baseline_cases.get(case_id)
        if baseline_passed and not current_passed:
            regressed.append(case_id)
            continue
        if not baseline_passed and current_passed:
            improved.append(case_id)
            continue
        unchanged.append(case_id)

    return EvalBaselineComparison(
        baseline_passed_cases=int(baseline_payload.get("passed_cases") or 0),
        current_passed_cases=int(current_report_payload.get("passed_cases") or 0),
        regressed_case_ids=regressed,
        improved_case_ids=improved,
        unchanged_case_ids=unchanged,
    )


def _normalize_string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    normalized = [str(item) for item in value if str(item).strip()]
    return normalized or None


def _normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _load_retrieval_eval_case(item: dict[str, object]) -> RetrievalEvalCase:
    filters_payload = item.get("filters") or {}
    case_id = _normalize_optional_string(item.get("id"))
    query = _normalize_optional_string(item.get("query"))
    if not case_id:
        raise ValueError("Retrieval eval case id is required.")
    if not query:
        raise ValueError(f"Retrieval eval case {case_id} must define a non-empty query.")
    return RetrievalEvalCase(
        case_id=case_id,
        query=query,
        filters=RetrievalFilters(
            source_types=_normalize_string_list(filters_payload.get("source_types")),
            created_at_from=_normalize_optional_string(filters_payload.get("created_at_from")),
            created_at_to=_normalize_optional_string(filters_payload.get("created_at_to")),
            knowledge_item_ids=_normalize_string_list(filters_payload.get("knowledge_item_ids")),
            keyword=_normalize_optional_string(filters_payload.get("keyword")),
            category=_normalize_optional_string(filters_payload.get("category")),
            user_tags=_normalize_string_list(filters_payload.get("user_tags")),
            ai_tags=_normalize_string_list(filters_payload.get("ai_tags")),
        ),
        expected_knowledge_item_ids=_normalize_string_list(item.get("expected_knowledge_item_ids"))
        or [],
        forbidden_knowledge_item_ids=_normalize_string_list(item.get("forbidden_knowledge_item_ids"))
        or [],
    )


def _validate_retrieval_eval_dataset(dataset: RetrievalEvalDataset) -> None:
    seen_case_ids: set[str] = set()
    for case in dataset.cases:
        if case.case_id in seen_case_ids:
            raise ValueError(f"Duplicate retrieval eval case id: {case.case_id}")
        seen_case_ids.add(case.case_id)
        if not case.expected_knowledge_item_ids and not case.forbidden_knowledge_item_ids:
            raise ValueError(
                f"Retrieval eval case {case.case_id} must define expected or forbidden knowledge item ids."
            )
        overlaps = sorted(
            set(case.expected_knowledge_item_ids) & set(case.forbidden_knowledge_item_ids)
        )
        if overlaps:
            overlap_text = ", ".join(overlaps)
            raise ValueError(
                f"Retrieval eval case {case.case_id} cannot declare the same knowledge item as expected and forbidden: {overlap_text}"
            )
