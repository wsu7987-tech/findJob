from __future__ import annotations

import pytest

from backend.app.services.retrieval_eval import (
    EvalBaselineComparison,
    RetrievalEvalCase,
    RetrievalEvalDataset,
    evaluate_retrieval,
    compare_eval_report_to_baseline,
    load_retrieval_eval_dataset,
    load_retrieval_eval_cases,
)
from backend.app.services.retrieval_types import (
    ChildChunkHit,
    ParentContext,
    RetrievalFilters,
    RetrievalResult,
)


def test_load_retrieval_eval_cases_reads_expected_and_forbidden_ids(app_paths) -> None:
    case_path = app_paths["app_data_dir"] / "retrieval-eval.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(
        """
[
  {
    "id": "alpha-report",
    "query": "alpha report",
    "filters": {
      "source_types": ["text"],
      "keyword": "alpha",
      "category": "research",
      "user_tags": ["alpha"],
      "ai_tags": ["report"]
    },
    "expected_knowledge_item_ids": ["ki-1"],
    "forbidden_knowledge_item_ids": ["ki-2"]
  }
]
""".strip(),
        encoding="utf-8",
    )

    cases = load_retrieval_eval_cases(case_path)

    assert cases == [
        RetrievalEvalCase(
            case_id="alpha-report",
            query="alpha report",
            filters=RetrievalFilters(
                source_types=["text"],
                keyword="alpha",
                category="research",
                user_tags=["alpha"],
                ai_tags=["report"],
            ),
            expected_knowledge_item_ids=["ki-1"],
            forbidden_knowledge_item_ids=["ki-2"],
        )
    ]


def test_load_retrieval_eval_dataset_supports_envelope_metadata(app_paths) -> None:
    case_path = app_paths["app_data_dir"] / "retrieval-eval-envelope.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(
        """
{
  "dataset_id": "phase1-baseline",
  "dataset_version": "v1",
  "owner": "rag-team",
  "notes": "real baseline cases",
  "cases": [
    {
      "id": "alpha-report",
      "query": "alpha report",
      "filters": {
        "source_types": ["text"]
      },
      "expected_knowledge_item_ids": ["ki-1"]
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    dataset = load_retrieval_eval_dataset(case_path)

    assert dataset == RetrievalEvalDataset(
        dataset_id="phase1-baseline",
        dataset_version="v1",
        owner="rag-team",
        notes="real baseline cases",
        cases=[
            RetrievalEvalCase(
                case_id="alpha-report",
                query="alpha report",
                filters=RetrievalFilters(source_types=["text"]),
                expected_knowledge_item_ids=["ki-1"],
                forbidden_knowledge_item_ids=[],
            )
        ],
    )


def test_load_retrieval_eval_dataset_rejects_duplicate_case_ids(app_paths) -> None:
    case_path = app_paths["app_data_dir"] / "retrieval-eval-duplicate.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(
        """
[
  {
    "id": "duplicate",
    "query": "alpha",
    "expected_knowledge_item_ids": ["ki-1"]
  },
  {
    "id": "duplicate",
    "query": "beta",
    "expected_knowledge_item_ids": ["ki-2"]
  }
]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate retrieval eval case id: duplicate"):
        load_retrieval_eval_dataset(case_path)


def test_load_retrieval_eval_dataset_requires_non_empty_and_non_overlapping_assertions(
    app_paths,
) -> None:
    empty_case_path = app_paths["app_data_dir"] / "retrieval-eval-empty-assertions.json"
    empty_case_path.parent.mkdir(parents=True, exist_ok=True)
    empty_case_path.write_text(
        """
[
  {
    "id": "missing-assertions",
    "query": "alpha"
  }
]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="Retrieval eval case missing-assertions must define expected or forbidden knowledge item ids.",
    ):
        load_retrieval_eval_dataset(empty_case_path)

    overlap_case_path = app_paths["app_data_dir"] / "retrieval-eval-overlap.json"
    overlap_case_path.write_text(
        """
[
  {
    "id": "overlap-case",
    "query": "alpha",
    "expected_knowledge_item_ids": ["ki-1"],
    "forbidden_knowledge_item_ids": ["ki-1"]
  }
]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="Retrieval eval case overlap-case cannot declare the same knowledge item as expected and forbidden: ki-1",
    ):
        load_retrieval_eval_dataset(overlap_case_path)


def test_evaluate_retrieval_flags_expected_hits_and_forbidden_recall() -> None:
    cases = [
        RetrievalEvalCase(
            case_id="alpha-pass",
            query="alpha",
            filters=RetrievalFilters(keyword="alpha"),
            expected_knowledge_item_ids=["ki-1"],
            forbidden_knowledge_item_ids=["ki-3"],
        ),
        RetrievalEvalCase(
            case_id="beta-fail",
            query="beta",
            filters=RetrievalFilters(keyword="beta"),
            expected_knowledge_item_ids=["ki-2"],
            forbidden_knowledge_item_ids=["ki-9"],
        ),
    ]

    def _fake_search(case: RetrievalEvalCase) -> RetrievalResult:
        if case.case_id == "alpha-pass":
            return RetrievalResult(
                query_text=case.query,
                filters=case.filters,
                child_hits=[
                    ChildChunkHit(
                        chunk_id="child-1",
                        knowledge_item_id="ki-1",
                        parent_chunk_id="parent-1",
                        section_title="Section A",
                        content="Alpha content",
                        source_type="text",
                        title="Alpha report",
                        source_name="alpha.txt",
                        source_value="alpha.txt",
                        created_at="2026-04-22T00:00:00Z",
                        category="research",
                        user_tags=["alpha"],
                        ai_tags=["report"],
                        vector_score=0.9,
                        metadata_keyword_score=1.0,
                        content_keyword_score=0.5,
                        final_score=0.885,
                    )
                ],
                parent_contexts={
                    "parent-1": ParentContext(
                        parent_chunk_id="parent-1",
                        knowledge_item_id="ki-1",
                        section_title="Section A",
                        content="Alpha context",
                        title="Alpha report",
                        source_type="text",
                        source_name="alpha.txt",
                        source_value="alpha.txt",
                        created_at="2026-04-22T00:00:00Z",
                        category="research",
                        user_tags=["alpha"],
                        ai_tags=["report"],
                    )
                },
            )
        return RetrievalResult(
            query_text=case.query,
            filters=case.filters,
            child_hits=[
                ChildChunkHit(
                    chunk_id="child-9",
                    knowledge_item_id="ki-9",
                    parent_chunk_id="parent-9",
                    section_title="Section B",
                    content="Wrong beta content",
                    source_type="text",
                    title="Wrong beta report",
                    source_name="beta.txt",
                    source_value="beta.txt",
                    created_at="2026-04-22T00:00:00Z",
                    category="ops",
                    user_tags=["beta"],
                    ai_tags=["wrong"],
                    vector_score=0.7,
                    metadata_keyword_score=0.5,
                    content_keyword_score=0.5,
                    final_score=0.625,
                )
            ],
            parent_contexts={},
        )

    report = evaluate_retrieval(cases=cases, search_case=_fake_search)

    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.failed_cases == 1
    assert report.case_reports[0].passed is True
    assert report.case_reports[0].missing_expected_knowledge_item_ids == []
    assert report.case_reports[0].unexpected_knowledge_item_ids == []
    assert report.case_reports[1].passed is False
    assert report.case_reports[1].missing_expected_knowledge_item_ids == ["ki-2"]
    assert report.case_reports[1].unexpected_knowledge_item_ids == ["ki-9"]


def test_compare_eval_report_to_baseline_detects_case_regressions() -> None:
    comparison = compare_eval_report_to_baseline(
        current_report_payload={
            "total_cases": 2,
            "passed_cases": 1,
            "failed_cases": 1,
            "case_reports": [
                {"case_id": "alpha", "passed": True},
                {"case_id": "beta", "passed": False},
            ],
        },
        baseline_payload={
            "total_cases": 2,
            "passed_cases": 2,
            "failed_cases": 0,
            "case_reports": [
                {"case_id": "alpha", "passed": True},
                {"case_id": "beta", "passed": True},
            ],
        },
    )

    assert comparison == EvalBaselineComparison(
        baseline_passed_cases=2,
        current_passed_cases=1,
        regressed_case_ids=["beta"],
        improved_case_ids=[],
        unchanged_case_ids=["alpha"],
    )
