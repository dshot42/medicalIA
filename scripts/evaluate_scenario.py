from __future__ import annotations

import json
from pathlib import Path

from app.container import get_container


def main() -> None:
    container = get_container()
    investigations = container.investigation_service.list("client_test-1")
    if not investigations:
        raise SystemExit("Aucune investigation terminée pour client_test-1.")
    latest = investigations[0]
    ground_truth_path = Path(__file__).parents[1] / "tests" / "evaluation" / "ground_truth.json"
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    differential = latest["final_assessment"]["differential_diagnoses"]
    expected = ground_truth["expected_diagnosis_for_post_run_evaluation_only"]
    rank = next(
        (item["rank"] for item in differential if item["disease_name"] == expected),
        None,
    )
    print(
        json.dumps(
            {
                "investigation_id": latest["investigation_id"],
                "expected_diagnosis_rank": rank,
                "top_1_match": rank == 1,
            },
            ensure_ascii=False,
        )
    )
    container.close()
    get_container.cache_clear()


if __name__ == "__main__":
    main()

