from __future__ import annotations

import json

from app.container import get_container


PATIENT_ID = "b.laura"
PROMPT = """Patient : Homme de 32 ans, sans antécédent médical notable.

Voyage : Bangladesh, août 2026, avec escale en Thaïlande.

Début des symptômes : 26 août 2026.

Le patient développe progressivement une maladie infectieuse inhabituelle après son retour en France."""


def main() -> None:
    container = get_container()
    try:
        filenames = {
            item.filename for item in container.document_service.list_documents(PATIENT_ID)
        }
        if "99_CORRIGE_NE_PAS_IMPORTER.md" in filenames:
            raise RuntimeError("Le document de correction est encore présent dans le corpus.")
        result = container.investigation_service.run(PATIENT_ID, PROMPT)
        diagnoses = result["final_assessment"]["differential_diagnoses"]
        summary = {
            "investigation_id": result["investigation_id"],
            "comparison_status": result["final_assessment"]["comparison_status"],
            "conclusion": result["final_assessment"]["conclusion"],
            "ranking": [
                {
                    "rank": item["rank"],
                    "hypothesis": item["disease_name"],
                    "diagnostic_status": item["diagnostic_status"],
                    "evidence_strength": item["evidence_strength_class"],
                    "primary_evidence": (
                        item["primary_evidence"]["observation"]
                        if item.get("primary_evidence")
                        else None
                    ),
                    "support_count": item["documentary_assessment"][
                        "validated_support_count"
                    ],
                    "contradiction_count": item["documentary_assessment"][
                        "validated_contradiction_count"
                    ],
                }
                for item in diagnoses
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        container.close()
        get_container.cache_clear()


if __name__ == "__main__":
    main()
