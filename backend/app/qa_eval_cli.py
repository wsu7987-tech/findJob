from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from backend.app.config import load_config
from backend.app.db import Database
from backend.app.schemas.qa import QAAnswerRequest
from backend.app.services.qa import answer_question
from backend.app.services.qa_eval import (
    compare_qa_eval_report_to_baseline,
    evaluate_qa,
    load_qa_eval_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline QA evaluation cases against the current backend QA pipeline."
    )
    parser.add_argument(
        "--cases",
        default="docs/testing/qa-eval-sample.json",
        help="Path to the QA eval case JSON file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max retrieval hits to request for each QA case.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the JSON report.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Optional path to an existing QA eval JSON report to compare against.",
    )
    args = parser.parse_args()

    config = load_config()
    database = Database(config.sqlite_path)
    database.initialize()
    dataset = load_qa_eval_dataset(args.cases)
    report = evaluate_qa(
        cases=dataset.cases,
        answer_case=lambda case: _answer_case(
            case=case,
            database=database,
            config=config,
            limit=args.limit,
        ),
    )
    payload = asdict(report)
    payload["dataset"] = {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "owner": dataset.owner,
        "notes": dataset.notes,
        "case_count": len(dataset.cases),
        "source_path": str(Path(args.cases)),
    }
    if args.baseline:
        baseline_payload = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        payload["baseline_comparison"] = asdict(
            compare_qa_eval_report_to_baseline(
                current_report_payload=payload,
                baseline_payload=baseline_payload,
            )
        )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)

    output_path = Path(args.output) if args.output else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")

    print(rendered)
    return 0 if report.failed_cases == 0 else 1


def _answer_case(*, case, database: Database, config, limit: int) -> dict[str, object]:
    session_id: str | None = None
    last_result: dict[str, object] | None = None
    for turn in case.turns:
        last_result = answer_question(
            db=database,
            config=config,
            payload=QAAnswerRequest(
                session_id=session_id,
                question=turn.question,
                mode=turn.mode,
                limit=limit,
                filters={
                    "source_types": turn.filters.source_types,
                    "created_at_from": turn.filters.created_at_from,
                    "created_at_to": turn.filters.created_at_to,
                    "knowledge_item_ids": turn.filters.knowledge_item_ids,
                    "keyword": turn.filters.keyword,
                    "category": turn.filters.category,
                    "user_tags": turn.filters.user_tags,
                    "ai_tags": turn.filters.ai_tags,
                },
            ),
        )
        session_id = str(last_result["session_id"])
    return last_result or {}


if __name__ == "__main__":
    raise SystemExit(main())
