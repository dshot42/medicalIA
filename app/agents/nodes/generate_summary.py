from __future__ import annotations

from collections import Counter

from app.agents.llm import StructuredLLM
from app.agents.state import AnalysisState
from app.models.event import MedicalEvent
from app.models.finding import Finding


def generate_summary_node(llm: StructuredLLM):
    def node(state: AnalysisState) -> dict[str, object]:
        events = [MedicalEvent.model_validate(item) for item in state.get("timeline", [])]
        findings = [Finding.model_validate(item) for item in state.get("findings", [])]
        categories = Counter(finding.category.value for finding in findings)
        deterministic = (
            f"L'analyse documentaire recense {len(events)} événement(s) et "
            f"{len(findings)} point(s) à clarifier. Ces observations ne constituent "
            "ni un diagnostic ni une preuve de causalité."
        )
        try:
            generated = llm.summarize(
                {
                    "event_count": len(events),
                    "finding_categories": dict(categories),
                    "verified_findings": sum(item.sources_verified for item in findings),
                    "instruction": "Synthèse documentaire prudente sans diagnostic.",
                }
            )
            summary = generated.summary if generated else deterministic
        except Exception:
            summary = deterministic + " Le service LLM optionnel n'était pas disponible."
        return {
            "summary": summary,
            "completed_nodes": [*state.get("completed_nodes", []), "GENERATE_SUMMARY"],
        }

    return node

