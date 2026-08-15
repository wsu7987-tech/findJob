from __future__ import annotations

import pytest

from backend.app.services.qa_eval import (
    QAEvalCase,
    QAEvalDataset,
    QAEvalBaselineComparison,
    QAEvalTurn,
    compare_qa_eval_report_to_baseline,
    evaluate_qa,
    load_qa_eval_cases,
    load_qa_eval_dataset,
)
from backend.app.services.retrieval_types import RetrievalFilters


def test_load_qa_eval_cases_reads_status_filters_and_assertions(app_paths) -> None:
    case_path = app_paths["app_data_dir"] / "qa-eval.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(
        """
[
  {
    "id": "alpha-answer",
    "question": "what is alpha",
    "filters": {
      "source_types": ["text"],
      "keyword": "alpha",
      "category": "research"
    },
    "expected_answer_status": "grounded",
    "expected_citation_knowledge_item_ids": ["ki-1"],
    "forbidden_citation_knowledge_item_ids": ["ki-2"],
    "expected_answer_substrings": ["alpha", "report"],
    "forbidden_answer_substrings": ["hallucinated"]
  }
]
""".strip(),
        encoding="utf-8",
    )

    cases = load_qa_eval_cases(case_path)

    assert cases == [
        QAEvalCase(
            case_id="alpha-answer",
            question="what is alpha",
            filters=RetrievalFilters(
                source_types=["text"],
                keyword="alpha",
                category="research",
            ),
            turns=[
                QAEvalTurn(
                    question="what is alpha",
                    mode="answer",
                    filters=RetrievalFilters(
                        source_types=["text"],
                        keyword="alpha",
                        category="research",
                    ),
                )
            ],
            expected_answer_status="grounded",
            expected_citation_knowledge_item_ids=["ki-1"],
            forbidden_citation_knowledge_item_ids=["ki-2"],
            expected_answer_substrings=["alpha", "report"],
            forbidden_answer_substrings=["hallucinated"],
        )
    ]


def test_load_qa_eval_dataset_supports_envelope_metadata(app_paths) -> None:
    case_path = app_paths["app_data_dir"] / "qa-eval-envelope.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(
        """
{
  "dataset_id": "phase4-qa-baseline",
  "dataset_version": "v1",
  "owner": "qa-team",
  "notes": "real qa baseline cases",
  "cases": [
    {
      "id": "alpha-answer",
      "question": "what is alpha",
      "expected_answer_status": "grounded",
      "expected_citation_knowledge_item_ids": ["ki-1"]
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    dataset = load_qa_eval_dataset(case_path)

    assert dataset == QAEvalDataset(
        dataset_id="phase4-qa-baseline",
        dataset_version="v1",
        owner="qa-team",
        notes="real qa baseline cases",
        cases=[
            QAEvalCase(
                case_id="alpha-answer",
                question="what is alpha",
                filters=RetrievalFilters(),
                turns=[
                    QAEvalTurn(
                        question="what is alpha",
                        mode="answer",
                        filters=RetrievalFilters(),
                    )
                ],
                expected_answer_status="grounded",
                expected_citation_knowledge_item_ids=["ki-1"],
                forbidden_citation_knowledge_item_ids=[],
                expected_answer_substrings=[],
                forbidden_answer_substrings=[],
            )
        ],
    )


def test_load_qa_eval_dataset_rejects_duplicate_case_ids(app_paths) -> None:
    case_path = app_paths["app_data_dir"] / "qa-eval-duplicate.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(
        """
[
  {
    "id": "duplicate",
    "question": "alpha",
    "expected_answer_status": "grounded"
  },
  {
    "id": "duplicate",
    "question": "beta",
    "expected_citation_knowledge_item_ids": ["ki-2"]
  }
]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate qa eval case id: duplicate"):
        load_qa_eval_dataset(case_path)


def test_load_qa_eval_dataset_requires_non_empty_question_and_assertions(app_paths) -> None:
    empty_question_path = app_paths["app_data_dir"] / "qa-eval-empty-question.json"
    empty_question_path.parent.mkdir(parents=True, exist_ok=True)
    empty_question_path.write_text(
        """
[
  {
    "id": "missing-question",
    "question": "   ",
    "expected_answer_status": "grounded"
  }
]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="QA eval case missing-question must define a non-empty question."):
        load_qa_eval_dataset(empty_question_path)

    missing_assertions_path = app_paths["app_data_dir"] / "qa-eval-missing-assertions.json"
    missing_assertions_path.write_text(
        """
[
  {
    "id": "missing-assertions",
    "question": "alpha"
  }
]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="QA eval case missing-assertions must define at least one expected or forbidden assertion.",
    ):
        load_qa_eval_dataset(missing_assertions_path)


def test_evaluate_qa_flags_status_citation_and_answer_text_failures() -> None:
    cases = [
        QAEvalCase(
            case_id="alpha-pass",
            question="what is alpha",
            filters=RetrievalFilters(keyword="alpha"),
            turns=[
                QAEvalTurn(
                    question="what is alpha",
                    mode="answer",
                    filters=RetrievalFilters(keyword="alpha"),
                )
            ],
            expected_answer_status="grounded",
            expected_citation_knowledge_item_ids=["ki-1"],
            forbidden_citation_knowledge_item_ids=["ki-9"],
            expected_answer_substrings=["alpha"],
            forbidden_answer_substrings=["hallucinated"],
        ),
        QAEvalCase(
            case_id="beta-fail",
            question="what is beta",
            filters=RetrievalFilters(keyword="beta"),
            turns=[
                QAEvalTurn(
                    question="what is beta",
                    mode="answer",
                    filters=RetrievalFilters(keyword="beta"),
                )
            ],
            expected_answer_status="grounded",
            expected_citation_knowledge_item_ids=["ki-2"],
            forbidden_citation_knowledge_item_ids=["ki-9"],
            expected_answer_substrings=["beta summary"],
            forbidden_answer_substrings=["wrong chapter"],
        ),
    ]

    def _fake_answer(case: QAEvalCase) -> dict[str, object]:
        if case.case_id == "alpha-pass":
            return {
                "question": case.question,
                "answer": "alpha report summary",
                "answer_status": "grounded",
                "confidence": 0.8,
                "applied_filters": {},
                "citations": [
                    {"citation_id": "cite-1", "knowledge_item_id": "ki-1"},
                ],
                "used_grounded_items": [],
                "suggested_queries": [],
            }
        return {
            "question": case.question,
            "answer": "wrong chapter answer",
            "answer_status": "insufficient_evidence",
            "confidence": 0.2,
            "applied_filters": {},
            "citations": [
                {"citation_id": "cite-9", "knowledge_item_id": "ki-9"},
            ],
            "used_grounded_items": [],
            "suggested_queries": ["rewrite beta"],
        }

    report = evaluate_qa(cases=cases, answer_case=_fake_answer)

    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.failed_cases == 1
    assert report.case_reports[0].passed is True
    assert report.case_reports[0].status_matched is True
    assert report.case_reports[0].missing_expected_citation_knowledge_item_ids == []
    assert report.case_reports[1].passed is False
    assert report.case_reports[1].status_matched is False
    assert report.case_reports[1].missing_expected_citation_knowledge_item_ids == ["ki-2"]
    assert report.case_reports[1].unexpected_citation_knowledge_item_ids == ["ki-9"]
    assert report.case_reports[1].missing_expected_answer_substrings == ["beta summary"]
    assert report.case_reports[1].unexpected_answer_substrings == ["wrong chapter"]


def test_load_qa_eval_dataset_supports_multi_turn_cases(app_paths) -> None:
    case_path = app_paths["app_data_dir"] / "qa-eval-multiturn.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(
        """
{
  "dataset_id": "phase4-qa-multiturn",
  "cases": [
    {
      "id": "vector-embedding-followup",
      "question": "什么是向量嵌入？",
      "turns": [
        {
          "question": "什么是向量嵌入？"
        },
        {
          "question": "它的三种距离度量是什么？",
          "mode": "knowledge_point",
          "filters": {
            "source_types": ["url"],
            "category": "research"
          }
        }
      ],
      "expected_answer_status": "grounded",
      "expected_answer_substrings": ["余弦相似度"]
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    dataset = load_qa_eval_dataset(case_path)

    assert dataset.cases[0].turns == [
        QAEvalTurn(
            question="什么是向量嵌入？",
            mode="answer",
            filters=RetrievalFilters(),
        ),
        QAEvalTurn(
            question="它的三种距离度量是什么？",
            mode="knowledge_point",
            filters=RetrievalFilters(source_types=["url"], category="research"),
        ),
    ]


def test_compare_qa_eval_report_to_baseline_detects_case_regressions() -> None:
    comparison = compare_qa_eval_report_to_baseline(
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

    assert comparison == QAEvalBaselineComparison(
        baseline_passed_cases=2,
        current_passed_cases=1,
        regressed_case_ids=["beta"],
        improved_case_ids=[],
        unchanged_case_ids=["alpha"],
    )
