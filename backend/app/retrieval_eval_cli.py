from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from backend.app.config import load_config
from backend.app.db import Database
from backend.app.services.retrieval import build_retrieval_context
from backend.app.services.retrieval_eval import (
    compare_eval_report_to_baseline,
    evaluate_retrieval,
    load_retrieval_eval_dataset,
)
from backend.app.services.retrieval_types import RetrievalQuery


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline retrieval evaluation cases against the current backend index."
    )
    parser.add_argument(
        "--cases",
        default="docs/testing/retrieval-eval-sample.json",
        help="Path to the retrieval eval case JSON file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max retrieval hits to request for each case.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the JSON report.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Optional path to an existing eval JSON report to compare against.",
    )
    args = parser.parse_args()

    config = load_config()
    database = Database(config.sqlite_path)
    database.initialize()
    dataset = load_retrieval_eval_dataset(args.cases)
    report = evaluate_retrieval(
        cases=dataset.cases,
        search_case=lambda case: build_retrieval_context(
            db=database,
            config=config,
            query=RetrievalQuery(
                text=case.query,
                filters=case.filters,
                limit=args.limit,
            ),
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
            compare_eval_report_to_baseline(
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


if __name__ == "__main__":
    raise SystemExit(main())
