from __future__ import annotations

from typing import TypedDict


class ClinicalInvestigationState(TypedDict, total=False):
    investigation_id: str
    parent_investigation_id: str | None
    patient_id: str
    consultation_symptoms: str
    investigation_mode: str
    document_inventory: list[dict[str, object]]
    inventory_attempt_count: int
    processed_document_ids: list[str]
    coverage_report: dict[str, object]
    parsed_consultation: dict[str, object]
    extracted_clinical_facts: list[dict[str, object]]
    retrieved_evidence: list[dict[str, object]]
    raw_clinical_evidence: list[dict[str, object]]
    retrieved_chunks: list[dict[str, object]]
    new_evidence: list[dict[str, object]]
    evidence_history: list[dict[str, object]]
    medical_knowledge_evidence: list[dict[str, object]]
    hypotheses: list[dict[str, object]]
    investigated_hypotheses: list[str]
    selected_hypothesis_id: str | None
    contradictions: list[dict[str, object]]
    discriminative_evidence: list[dict[str, object]]
    missing_information: list[str]
    iteration_count: int
    max_iterations: int
    llm_call_count: int
    max_llm_calls: int
    timeout_seconds: int
    started_at: str
    investigation_history: list[dict[str, object]]
    targeted_queries: list[str]
    last_new_evidence_count: int
    stop_reason: str | None
    final_assessment: dict[str, object] | None
    qwen_comparison: dict[str, object]
    comparison_status: dict[str, object]
    technical_warnings: list[str]
    evidence_revision: int
    rejected_evidence_ids: list[str]
    hypothesis_generation_round: int
    new_clinical_direction: bool

