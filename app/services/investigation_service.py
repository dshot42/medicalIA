from __future__ import annotations

from app.core.config import Settings
from app.investigation.graph import ClinicalInvestigationGraph
from app.storage.repository import Repository


class ClinicalInvestigationService:
    def __init__(
        self,
        repository: Repository,
        graph: ClinicalInvestigationGraph,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.graph = graph
        self.settings = settings

    @property
    def active_model_version(self) -> str:
        local_llm = self.graph.local_llm
        if local_llm is not None and local_llm.enabled:
            return f"{self.settings.investigation_model_version}+{local_llm.model_identifier}"
        return f"{self.settings.investigation_model_version}+deterministic"

    def run(
        self,
        patient_id: str,
        consultation_symptoms: str,
        max_iterations: int | None = None,
        parent_investigation_id: str | None = None,
    ) -> dict[str, object]:
        if patient_id not in self.repository.list_patients():
            raise ValueError("Patient inconnu ou sans document indexé.")
        symptoms = consultation_symptoms.strip()
        if len(symptoms) < 10:
            raise ValueError("Le motif de consultation est trop court pour être analysé.")
        iteration_budget = min(
            max_iterations or self.settings.max_investigation_iterations,
            self.settings.max_investigation_iterations,
        )
        state = self.graph.run(
            patient_id=patient_id,
            consultation_symptoms=symptoms,
            max_iterations=iteration_budget,
            max_llm_calls=self.settings.max_investigation_llm_calls,
            timeout_seconds=self.settings.investigation_timeout_seconds,
            parent_investigation_id=parent_investigation_id,
        )
        final = state.get("final_assessment")
        persisted_status = "incomplete"
        if final:
            persisted_status = (
                "completed_with_error"
                if final.get("comparison_status") == "failed"
                else "completed"
            )
        self.repository.save_investigation(
            investigation_id=state["investigation_id"],
            patient_id=patient_id,
            symptoms=symptoms,
            status=persisted_status,
            model_version=self.active_model_version,
            state=dict(state),
            final_assessment=final,
            parent_investigation_id=parent_investigation_id,
        )
        return {
            "investigation_id": state["investigation_id"],
            "patient_id": patient_id,
            "consultation_symptoms": symptoms,
            "investigation_mode": state.get("investigation_mode", "grounded"),
            "model_version": self.active_model_version,
            "iteration_count": state["iteration_count"],
            "llm_call_count": state.get("llm_call_count", 0),
            "stop_reason": state.get("stop_reason"),
            "parsed_consultation": state.get("parsed_consultation"),
            "document_inventory": state.get("document_inventory", []),
            "coverage_report": state.get("coverage_report", {}),
            "clinical_evidence": state.get("extracted_clinical_facts", []),
            "discriminative_evidence": state.get("discriminative_evidence", []),
            "medical_knowledge_evidence": state.get("medical_knowledge_evidence", []),
            "hypotheses": state.get("hypotheses", []),
            "retrieved_chunks": state.get("retrieved_chunks", []),
            "raw_clinical_evidence": state.get("raw_clinical_evidence", []),
            "new_evidence": state.get("new_evidence", []),
            "evidence_history": state.get("evidence_history", []),
            "rejected_evidence_ids": state.get("rejected_evidence_ids", []),
            "investigation_history": state.get("investigation_history", []),
            "targeted_queries": state.get("targeted_queries", []),
            "qwen_comparison": state.get("qwen_comparison", {}),
            "comparison_status": state.get("comparison_status", {}),
            "technical_warnings": state.get("technical_warnings", []),
            "final_assessment": final,
        }

    def rerun(
        self, investigation_id: str, patient_id: str
    ) -> dict[str, object]:
        previous = self.repository.get_investigation(investigation_id, patient_id)
        if previous is None:
            raise ValueError("Investigation introuvable.")
        return self.run(
            patient_id=str(previous["patient_id"]),
            consultation_symptoms=str(previous["consultation_symptoms"]),
            parent_investigation_id=investigation_id,
        )

    def list(self, patient_id: str) -> list[dict[str, object]]:
        return self.repository.list_investigations(patient_id)

    def get(
        self, investigation_id: str, patient_id: str
    ) -> dict[str, object] | None:
        return self.repository.get_investigation(investigation_id, patient_id)

    def get_hypothesis_evidence(
        self,
        investigation_id: str,
        hypothesis_id: str,
        patient_id: str,
    ) -> dict[str, object] | None:
        investigation = self.repository.get_investigation(investigation_id, patient_id)
        if investigation is None:
            return None
        state = investigation["state"]
        hypothesis = next(
            (
                item
                for item in state.get("hypotheses", [])
                if item.get("hypothesis_id") == hypothesis_id
            ),
            None,
        )
        if hypothesis is None:
            return None
        relations = hypothesis.get("evidence_relations", [])
        patient_ids = set(hypothesis.get("supporting_evidence_ids", [])) | set(
            hypothesis.get("contradicting_evidence_ids", [])
        )
        knowledge_ids = set(hypothesis.get("knowledge_evidence_ids", []))
        for relation in relations:
            evidence_id = relation.get("evidence_id")
            if evidence_id:
                patient_ids.add(evidence_id)
            patient_ids.update(relation.get("related_evidence_ids", []))
            knowledge_ids.update(relation.get("knowledge_ids", []))
        return {
            "hypothesis": hypothesis,
            "evidence_relations": relations,
            "assertions": hypothesis.get("assertions", []),
            "patient_evidence": [
                item
                for item in state.get("extracted_clinical_facts", [])
                if item.get("evidence_id") in patient_ids
            ],
            "medical_knowledge_evidence": [
                item
                for item in state.get("medical_knowledge_evidence", [])
                if item.get("knowledge_id") in knowledge_ids
            ],
        }

