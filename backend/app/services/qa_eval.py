from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.app.services.retrieval_types import RetrievalFilters


@dataclass(slots=True)
class QAEvalTurn:
    question: str
    mode: str
    filters: RetrievalFilters


@dataclass(slots=True)
class QAEvalCase:
    case_id: str
    question: str
    filters: RetrievalFilters
    turns: list[QAEvalTurn]
    expected_answer_status: str | None
    expected_citation_knowledge_item_ids: list[str]
    forbidden_citation_knowledge_item_ids: list[str]
    expected_answer_substrings: list[str]
    forbidden_answer_substrings: list[str]


@dataclass(slots=True)
class QAEvalDataset:
    dataset_id: str | None
    dataset_version: str | None
    owner: str | None
    notes: str | None
    cases: list[QAEvalCase]


@dataclass(slots=True)
class QAEvalCaseReport:
    case_id: str
    question: str
    answer_status: str
    status_matched: bool
    answer: str
    returned_citation_knowledge_item_ids: list[str]
    missing_expected_citation_knowledge_item_ids: list[str]
    unexpected_citation_knowledge_item_ids: list[str]
    missing_expected_answer_substrings: list[str]
    unexpected_answer_substrings: list[str]
    passed: bool


@dataclass(slots=True)
class QAEvalReport:
    total_cases: int
    passed_cases: int
    failed_cases: int
    case_reports: list[QAEvalCaseReport]


@dataclass(slots=True)
class QAEvalBaselineComparison:
    baseline_passed_cases: int
    current_passed_cases: int
    regressed_case_ids: list[str]
    improved_case_ids: list[str]
    unchanged_case_ids: list[str]


def load_qa_eval_dataset(path: str | Path) -> QAEvalDataset:
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
        raise ValueError("QA eval payload must be a JSON array or an object with a cases field.")

    dataset = QAEvalDataset(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        owner=owner,
        notes=notes,
        cases=[_load_qa_eval_case(item) for item in cases_payload],
    )
    _validate_qa_eval_dataset(dataset)
    return dataset


def load_qa_eval_cases(path: str | Path) -> list[QAEvalCase]:
    return load_qa_eval_dataset(path).cases


def evaluate_qa(
    *,
    cases: list[QAEvalCase],
    answer_case: Callable[[QAEvalCase], dict[str, object]],
) -> QAEvalReport:
    case_reports: list[QAEvalCaseReport] = []
    for case in cases:
        result = answer_case(case)
        answer_status = _normalize_optional_string(result.get("answer_status")) or "insufficient_evidence"
        answer_text = _normalize_optional_string(result.get("answer")) or ""
        returned_ids = _dedupe_preserve_order(
            [
                str(citation.get("knowledge_item_id"))
                for citation in list(result.get("citations") or [])
                if isinstance(citation, dict) and str(citation.get("knowledge_item_id") or "").strip()
            ]
        )
        missing_expected_ids = [
            knowledge_item_id
            for knowledge_item_id in case.expected_citation_knowledge_item_ids
            if knowledge_item_id not in returned_ids
        ]
        unexpected_ids = [
            knowledge_item_id
            for knowledge_item_id in case.forbidden_citation_knowledge_item_ids
            if knowledge_item_id in returned_ids
        ]
        lower_answer = answer_text.lower()
        missing_expected_substrings = [
            entry
            for entry in case.expected_answer_substrings
            if entry.lower() not in lower_answer
        ]
        unexpected_substrings = [
            entry
            for entry in case.forbidden_answer_substrings
            if entry.lower() in lower_answer
        ]
        status_matched = (
            case.expected_answer_status is None
            or answer_status == case.expected_answer_status
        )
        case_reports.append(
            QAEvalCaseReport(
                case_id=case.case_id,
                question=case.question,
                answer_status=answer_status,
                status_matched=status_matched,
                answer=answer_text,
                returned_citation_knowledge_item_ids=returned_ids,
                missing_expected_citation_knowledge_item_ids=missing_expected_ids,
                unexpected_citation_knowledge_item_ids=unexpected_ids,
                missing_expected_answer_substrings=missing_expected_substrings,
                unexpected_answer_substrings=unexpected_substrings,
                passed=(
                    status_matched
                    and not missing_expected_ids
                    and not unexpected_ids
                    and not missing_expected_substrings
                    and not unexpected_substrings
                ),
            )
        )

    passed_cases = sum(1 for item in case_reports if item.passed)
    total_cases = len(case_reports)
    return QAEvalReport(
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=total_cases - passed_cases,
        case_reports=case_reports,
    )


def compare_qa_eval_report_to_baseline(
    *,
    current_report_payload: dict[str, object],
    baseline_payload: dict[str, object],
) -> QAEvalBaselineComparison:
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

    return QAEvalBaselineComparison(
        baseline_passed_cases=int(baseline_payload.get("passed_cases") or 0),
        current_passed_cases=int(current_report_payload.get("passed_cases") or 0),
        regressed_case_ids=regressed,
        improved_case_ids=improved,
        unchanged_case_ids=unchanged,
    )


def _normalize_string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    normalized = [str(item).strip() for item in value if str(item).strip()]
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


def _load_qa_eval_case(item: dict[str, object]) -> QAEvalCase:
    case_id = _normalize_optional_string(item.get("id"))
    question = _normalize_optional_string(item.get("question"))
    if not case_id:
        raise ValueError("QA eval case id is required.")
    if not question:
        raise ValueError(f"QA eval case {case_id} must define a non-empty question.")
    return QAEvalCase(
        case_id=case_id,
        question=question,
        filters=_load_retrieval_filters(item.get("filters") or {}),
        turns=_load_qa_eval_turns(item, fallback_question=question),
        expected_answer_status=_normalize_optional_string(item.get("expected_answer_status")),
        expected_citation_knowledge_item_ids=_normalize_string_list(
            item.get("expected_citation_knowledge_item_ids")
        )
        or [],
        forbidden_citation_knowledge_item_ids=_normalize_string_list(
            item.get("forbidden_citation_knowledge_item_ids")
        )
        or [],
        expected_answer_substrings=_normalize_string_list(item.get("expected_answer_substrings")) or [],
        forbidden_answer_substrings=_normalize_string_list(item.get("forbidden_answer_substrings")) or [],
    )


def _validate_qa_eval_dataset(dataset: QAEvalDataset) -> None:
    seen_case_ids: set[str] = set()
    for case in dataset.cases:
        if case.case_id in seen_case_ids:
            raise ValueError(f"Duplicate qa eval case id: {case.case_id}")
        seen_case_ids.add(case.case_id)
        if not case.turns:
            raise ValueError(f"QA eval case {case.case_id} must define at least one turn.")
        has_assertions = any(
            [
                case.expected_answer_status is not None,
                bool(case.expected_citation_knowledge_item_ids),
                bool(case.forbidden_citation_knowledge_item_ids),
                bool(case.expected_answer_substrings),
                bool(case.forbidden_answer_substrings),
            ]
        )
        if not has_assertions:
            raise ValueError(
                f"QA eval case {case.case_id} must define at least one expected or forbidden assertion."
            )
        overlaps = sorted(
            set(case.expected_citation_knowledge_item_ids)
            & set(case.forbidden_citation_knowledge_item_ids)
        )
        if overlaps:
            overlap_text = ", ".join(overlaps)
            raise ValueError(
                f"QA eval case {case.case_id} cannot declare the same citation knowledge item as expected and forbidden: {overlap_text}"
            )


def _load_qa_eval_turns(
    item: dict[str, object],
    *,
    fallback_question: str,
) -> list[QAEvalTurn]:
    turns_payload = item.get("turns")
    if not isinstance(turns_payload, list) or not turns_payload:
        return [
            QAEvalTurn(
                question=fallback_question,
                mode="answer",
                filters=_load_retrieval_filters(item.get("filters") or {}),
            )
        ]

    turns: list[QAEvalTurn] = []
    for index, turn_payload in enumerate(turns_payload, start=1):
        if not isinstance(turn_payload, dict):
            raise ValueError(
                f"QA eval case {item.get('id') or '<unknown>'} turn {index} must be an object."
            )
        question = _normalize_optional_string(turn_payload.get("question"))
        if not question:
            raise ValueError(
                f"QA eval case {item.get('id') or '<unknown>'} turn {index} must define a non-empty question."
            )
        turns.append(
            QAEvalTurn(
                question=question,
                mode=_normalize_optional_string(turn_payload.get("mode")) or "answer",
                filters=_load_retrieval_filters(turn_payload.get("filters") or {}),
            )
        )
    return turns


def _load_retrieval_filters(filters_payload: object) -> RetrievalFilters:
    if not isinstance(filters_payload, dict):
        return RetrievalFilters()
    return RetrievalFilters(
        source_types=_normalize_string_list(filters_payload.get("source_types")),
        created_at_from=_normalize_optional_string(filters_payload.get("created_at_from")),
        created_at_to=_normalize_optional_string(filters_payload.get("created_at_to")),
        knowledge_item_ids=_normalize_string_list(filters_payload.get("knowledge_item_ids")),
        keyword=_normalize_optional_string(filters_payload.get("keyword")),
        category=_normalize_optional_string(filters_payload.get("category")),
        user_tags=_normalize_string_list(filters_payload.get("user_tags")),
        ai_tags=_normalize_string_list(filters_payload.get("ai_tags")),
    )
