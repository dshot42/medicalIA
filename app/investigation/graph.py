from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.agents.local_gguf import (
    LocalGGUFClient,
    QwenHypothesisReview,
)
from app.investigation.discrimination import identify_discriminative_findings
from app.investigation.evidence import (
    evidence_semantically_duplicate,
    extract_evidence_from_chunks,
    extract_raw_evidence_from_chunks,
    verify_patient_evidence,
    verify_raw_evidence,
)
from app.investigation.knowledge import (
    MedicalKnowledgeService,
    contradiction_match,
    text_match,
)
from app.investigation.parsing import parse_consultation_text
from app.investigation.retrieval import PatientHistoryRetriever
from app.investigation.state import ClinicalInvestigationState
from app.investigation.validation import (
    build_validated_assertions,
    relation_ids,
    render_validated_narrative,
    validate_evidence_relations,
)
from app.models.document import DocumentChunk
from app.models.investigation import (
    ComparativeAssessment,
    ConfidenceLevel,
    CoverageReport,
    DiagnosticStatus,
    DiagnosticUncertainty,
    DiagnosticHypothesis,
    DiscriminativeCategory,
    DiscriminativeEvidence,
    DocumentaryAssessment,
    EvidencePolarity,
    EvidenceRelation,
    EvidenceSpecificity,
    EvidenceStrengthClass,
    EvidenceTier,
    EvidenceType,
    FinalAssessment,
    InvestigationHistoryEntry,
    InvestigationStatus,
    MedicalKnowledgeEvidence,
    MedicalKnowledgeProfile,
    ParsedConsultation,
    PatientEvidence,
    RankedDiagnosis,
    RankedEvidence,
    RelationValidationStatus,
    VerificationStatus,
)
from app.storage.repository import Repository

LOGGER = logging.getLogger(__name__)


def _history(
    state: ClinicalInvestigationState,
    node: str,
    details: str,
    hypothesis_id: str | None = None,
    query: str | None = None,
    new_evidence_ids: list[str] | None = None,
) -> list[dict[str, object]]:
    entry = InvestigationHistoryEntry(
        node=node,
        iteration=state.get("iteration_count", 0),
        hypothesis_id=hypothesis_id,
        query=query,
        details=details,
        new_evidence_ids=new_evidence_ids or [],
    )
    return [*state.get("investigation_history", []), entry.model_dump(mode="json")]


def _hypothesis_id(disease_name: str) -> str:
    return f"hyp_{hashlib.sha256(disease_name.encode()).hexdigest()[:12]}"


def _validate_documentary_status(
    hypothesis: DiagnosticHypothesis,
    proposed_status: DiagnosticStatus | None = None,
    proposed_strength: EvidenceStrengthClass | None = None,
    proposed_uncertainty: DiagnosticUncertainty | None = None,
) -> None:
    """Validate an LLM proposal against the independently classified relations."""

    supports = [
        relation for relation in hypothesis.evidence_relations
        if relation.relation == EvidenceRelation.SUPPORTS
        and relation.validation_status == RelationValidationStatus.VALIDATED
    ]
    confirmatory = [
        relation for relation in supports
        if relation.evidence_tier == EvidenceTier.CONFIRMATORY
    ]
    discriminating = [
        relation for relation in supports
        if relation.evidence_tier == EvidenceTier.HIGHLY_DISCRIMINATING
    ]
    contradictions = [
        relation for relation in hypothesis.evidence_relations
        if relation.relation in {
            EvidenceRelation.CONTRADICTS,
            EvidenceRelation.DOCUMENT_CONFLICT,
        }
        and relation.validation_status == RelationValidationStatus.VALIDATED
    ]
    confirmatory_conflict = any(
        conflict.criterion
        and any(item.criterion == conflict.criterion for item in confirmatory)
        for conflict in contradictions
    )

    if confirmatory and not confirmatory_conflict:
        status = DiagnosticStatus.DOCUMENTED_CONFIRMED
        strength = EvidenceStrengthClass.DECISIVE
        uncertainty = DiagnosticUncertainty.LOW_FOR_DOCUMENTED_CONFIRMATION
    elif discriminating:
        allowed_statuses = {
            DiagnosticStatus.STRONGLY_SUPPORTED,
            DiagnosticStatus.SUPPORTED,
            DiagnosticStatus.POSSIBLE,
        }
        status = (
            proposed_status
            if proposed_status in allowed_statuses
            else DiagnosticStatus.STRONGLY_SUPPORTED
        )
        strength = (
            proposed_strength
            if proposed_strength in {
                EvidenceStrengthClass.HIGHLY_DISCRIMINATING,
                EvidenceStrengthClass.SPECIFIC,
            }
            else EvidenceStrengthClass.HIGHLY_DISCRIMINATING
        )
        uncertainty = proposed_uncertainty or DiagnosticUncertainty.MODERATE
    elif supports:
        allowed_statuses = {DiagnosticStatus.SUPPORTED, DiagnosticStatus.POSSIBLE}
        status = (
            proposed_status
            if proposed_status in allowed_statuses
            else DiagnosticStatus.POSSIBLE
        )
        strength = EvidenceStrengthClass.NON_SPECIFIC
        uncertainty = proposed_uncertainty or DiagnosticUncertainty.HIGH
    elif contradictions:
        status = DiagnosticStatus.CONTRADICTED
        strength = EvidenceStrengthClass.CONTRADICTORY
        uncertainty = proposed_uncertainty or DiagnosticUncertainty.MODERATE
    else:
        status = DiagnosticStatus.INSUFFICIENT_EVIDENCE
        strength = EvidenceStrengthClass.INSUFFICIENT
        uncertainty = DiagnosticUncertainty.UNDETERMINED

    hypothesis.diagnostic_status = status
    hypothesis.evidence_strength_class = strength
    hypothesis.uncertainty = uncertainty
    hypothesis.confidence_level = {
        DiagnosticStatus.DOCUMENTED_CONFIRMED: ConfidenceLevel.STRONGLY_SUPPORTED,
        DiagnosticStatus.STRONGLY_SUPPORTED: ConfidenceLevel.STRONGLY_SUPPORTED,
        DiagnosticStatus.SUPPORTED: ConfidenceLevel.PLAUSIBLE,
        DiagnosticStatus.POSSIBLE: ConfidenceLevel.WEAKLY_SUPPORTED,
        DiagnosticStatus.CONTRADICTED: ConfidenceLevel.WEAKLY_SUPPORTED,
        DiagnosticStatus.INSUFFICIENT_EVIDENCE: ConfidenceLevel.NOT_ASSESSABLE,
    }[status]


def _ranked_evidence(
    relation: object,
    facts: dict[str, PatientEvidence],
) -> RankedEvidence:
    evidence_ids = list(dict.fromkeys([
        relation.evidence_id,
        *relation.related_evidence_ids,
    ]))
    sources = list(dict.fromkeys(
        occurrence.filename
        for evidence_id in evidence_ids
        if evidence_id in facts
        for occurrence in (
            facts[evidence_id].source_occurrences
            or []
        )
    ))
    if not sources:
        sources = list(dict.fromkeys(
            facts[evidence_id].filename
            for evidence_id in evidence_ids
            if evidence_id in facts
        ))
    return RankedEvidence(
        evidence_id=relation.evidence_id,
        type=relation.evidence_tier,
        observation=relation.patient_observation,
        source_id=sources[0] if sources else None,
        source_ids=sources,
        criterion=relation.criterion,
        specificity=relation.specificity,
        validation=relation.validation_status,
        rationale=relation.classification_rationale or relation.rationale,
    )


def _apply_validated_documentary_review(
    hypothesis: DiagnosticHypothesis,
    facts: list[PatientEvidence],
    profile: MedicalKnowledgeProfile | None,
    review: QwenHypothesisReview | None = None,
    *,
    include_undocumented_tests: bool = False,
) -> DiagnosticHypothesis:
    """Make structured relations, rather than LLM prose, the source of truth."""

    relations = validate_evidence_relations(
        facts,
        profile,
        hypothesis_id=hypothesis.hypothesis_id,
        proposed_supporting_ids=(review.supporting_evidence_ids if review else []),
        proposed_contradicting_ids=(review.contradicting_evidence_ids if review else []),
        proposed_discriminative_ids=(review.discriminative_evidence_ids if review else []),
        include_undocumented_tests=include_undocumented_tests,
    )
    hypothesis.evidence_relations = relations
    hypothesis.supporting_evidence_ids = relation_ids(
        relations, EvidenceRelation.SUPPORTS
    )
    hypothesis.contradicting_evidence_ids = relation_ids(
        relations, EvidenceRelation.CONTRADICTS
    )
    hypothesis.discriminative_evidence_ids = sorted(
        {
            relation.evidence_id
            for relation in relations
            if relation.relevant
            and relation.relation
            in {
                EvidenceRelation.SUPPORTS,
                EvidenceRelation.CONTRADICTS,
                EvidenceRelation.DOCUMENT_CONFLICT,
            }
        }
    )
    missing_relations = [
        relation
        for relation in relations
        if relation.relation == EvidenceRelation.MISSING
    ]
    hypothesis.missing_information = list(
        dict.fromkeys(
            relation.patient_observation or relation.criterion or "Information non documentée"
            for relation in missing_relations
        )
    )
    _validate_documentary_status(hypothesis)

    dated = {
        fact.date
        for fact in facts
        if fact.date is not None
        and fact.evidence_id
        in {
            relation.evidence_id for relation in relations if relation.relevant
        }
    }
    temporal_facts = {
        fact.evidence_id
        for fact in facts
        if fact.type in {EvidenceType.TEMPORAL, EvidenceType.DURATION, EvidenceType.TRIGGER}
    }
    hypothesis.chronology_assessed = len(dated) >= 2 and any(
        relation.evidence_id in temporal_facts for relation in relations
    )
    hypothesis.contradiction_search_completed = profile is not None
    hypothesis.investigation_status = InvestigationStatus.INVESTIGATED
    hypothesis.reasoning_summary = render_validated_narrative(
        hypothesis.disease_name, relations
    )
    hypothesis.assertions = build_validated_assertions(
        hypothesis.hypothesis_id, hypothesis.disease_name, relations
    )
    return hypothesis


class ClinicalInvestigationGraph:
    NODE_NAMES = [
        "parse_consultation",
        "generate_llm_only_differential",
        "rank_llm_only_differential",
        "generate_qwen_grounded_differential",
        "select_qwen_hypothesis",
        "retrieve_qwen_targeted_evidence",
        "review_qwen_hypothesis",
        "qwen_should_continue",
        "rank_qwen_differential",
        "inventory_patient_documents",
        "extract_clinical_evidence",
        "control_document_coverage",
        "identify_discriminative_evidence",
        "generate_differential_diagnoses",
        "select_hypothesis",
        "retrieve_targeted_evidence",
        "verify_evidence",
        "expand_differential_diagnoses",
        "challenge_hypothesis",
        "update_hypothesis",
        "compare_hypotheses",
        "should_continue_investigation",
        "rank_differential_diagnoses",
    ]

    def __init__(
        self,
        repository: Repository,
        patient_retriever: PatientHistoryRetriever,
        knowledge: MedicalKnowledgeService | None,
        checkpoint_path: Path,
        model_version: str,
        investigation_mode: str = "grounded",
        local_llm: LocalGGUFClient | None = None,
    ) -> None:
        self.repository = repository
        self.patient_retriever = patient_retriever
        self.knowledge = knowledge
        self.model_version = model_version
        self.investigation_mode = investigation_mode
        self.local_llm = local_llm
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self._connection)
        self.graph = self._compile()

    def parse_consultation(self, state: ClinicalInvestigationState) -> dict[str, object]:
        parsed = parse_consultation_text(state["consultation_symptoms"])
        llm_calls = state.get("llm_call_count", 0)
        llm_status = "repli déterministe (LLM désactivé ou budget nul)"
        technical_warning: str | None = None
        if (
            self.local_llm is not None
            and self.local_llm.enabled
            and llm_calls < state.get("max_llm_calls", 0)
        ):
            llm_calls += 1
            try:
                parsed = self.local_llm.parse_consultation(
                    state["consultation_symptoms"], parsed
                )
                llm_status = (
                    f"extraction structurée par {self.local_llm.model_identifier}, "
                    "puis validation contre le texte source"
                )
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                LOGGER.warning(
                    "clinical_investigation_llm_fallback",
                    extra={
                        "event": "clinical_investigation_llm_fallback",
                        "operation": "parse_consultation",
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:300],
                    },
                )
                llm_status = (
                    f"sortie GGUF rejetée ({type(exc).__name__}) ; "
                    "repli déterministe appliqué"
                )
                technical_warning = (
                    f"parse_consultation:{type(exc).__name__}: sortie LLM rejetée; "
                    "extraction déterministe utilisée."
                )
        return {
            "parsed_consultation": parsed.model_dump(mode="json"),
            "missing_information": parsed.missing_details,
            "llm_call_count": llm_calls,
            "technical_warnings": [
                *state.get("technical_warnings", []),
                *([technical_warning] if technical_warning else []),
            ],
            "investigation_history": _history(
                state,
                "parse_consultation",
                f"{len(parsed.symptoms)} symptôme(s) structuré(s), sans compléter les "
                f"absences ; {llm_status}.",
            ),
        }

    def retrieve_patient_history(self, state: ClinicalInvestigationState) -> dict[str, object]:
        parsed = ParsedConsultation.model_validate(state["parsed_consultation"])
        chunks, queries = self.patient_retriever.initial_search(state["patient_id"], parsed)
        return {
            "retrieved_evidence": [chunk.model_dump(mode="json") for chunk in chunks],
            "targeted_queries": queries,
            "investigation_history": _history(
                state,
                "retrieve_patient_history",
                f"{len(chunks)} passage(s) retrouvés, tous filtrés pour {state['patient_id']}.",
                query=" | ".join(queries),
            ),
        }

    def inventory_patient_documents(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        inventory, chunks = self.patient_retriever.inventory(state["patient_id"])
        return {
            "document_inventory": inventory,
            "retrieved_evidence": [chunk.model_dump(mode="json") for chunk in chunks],
            "inventory_attempt_count": state.get("inventory_attempt_count", 0) + 1,
            "processed_document_ids": [],
            "investigation_history": _history(
                state,
                "inventory_patient_documents",
                f"Inventaire autoritatif : {len(inventory)} document(s), "
                f"{len(chunks)} chunk(s), indépendamment du classement vectoriel.",
            ),
        }

    def generate_llm_only_differential(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        llm_calls = state.get("llm_call_count", 0)
        hypotheses: list[DiagnosticHypothesis] = []
        status = "Le modèle GGUF local est indisponible ou son budget est épuisé."
        technical_warning: str | None = None
        if (
            self.local_llm is not None
            and self.local_llm.enabled
            and llm_calls < state.get("max_llm_calls", 0)
        ):
            llm_calls += 1
            try:
                candidates = self.local_llm.generate_llm_only_differential(
                    state["consultation_symptoms"]
                )
                for candidate in candidates:
                    hypotheses.append(
                        DiagnosticHypothesis(
                            hypothesis_id=_hypothesis_id(candidate.disease_name),
                            disease_name=candidate.disease_name,
                            missing_information=candidate.missing_information,
                            investigation_status=InvestigationStatus.NEEDS_CLINICIAN,
                            confidence_level=ConfidenceLevel.NOT_ASSESSABLE,
                            reasoning_summary=(
                                "Hypothèse non sourcée produite par le LLM local à partir des "
                                f"seuls symptômes fournis. {candidate.reasoning_summary}"
                            ),
                            comparison_score=0,
                            llm_supporting_quotes=candidate.supporting_symptom_quotes,
                        )
                    )
                status = (
                    f"{len(hypotheses)} hypothèse(s) générée(s) par "
                    f"{self.local_llm.model_identifier} à partir du seul motif de consultation."
                )
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                LOGGER.warning(
                    "clinical_investigation_llm_fallback",
                    extra={
                        "event": "clinical_investigation_llm_fallback",
                        "operation": "generate_llm_only_differential",
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:300],
                    },
                )
                status = (
                    "La sortie GGUF a été rejetée "
                    f"({type(exc).__name__}) ; aucune hypothèse n'a été inventée."
                )
                technical_warning = (
                    f"generate_llm_only_differential:{type(exc).__name__}: sortie rejetée."
                )
        else:
            technical_warning = (
                "generate_llm_only_differential:UnavailableOrBudgetExceeded: "
                "aucune hypothèse générée."
            )
        return {
            "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
            "extracted_clinical_facts": [],
            "retrieved_evidence": [],
            "retrieved_chunks": [],
            "raw_clinical_evidence": [],
            "new_evidence": [],
            "evidence_history": [],
            "evidence_revision": 0,
            "last_generated_evidence_revision": 0,
            "hypothesis_generation_round": 0,
            "new_clinical_direction": False,
            "medical_knowledge_evidence": [],
            "investigated_hypotheses": [item.hypothesis_id for item in hypotheses],
            "llm_call_count": llm_calls,
            "technical_warnings": [
                *state.get("technical_warnings", []),
                *([technical_warning] if technical_warning else []),
            ],
            "stop_reason": "Mode LLM seul : aucun dossier ni référentiel consulté.",
            "investigation_history": _history(
                state,
                "generate_llm_only_differential",
                status,
            ),
        }

    def rank_llm_only_differential(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        hypotheses = [
            DiagnosticHypothesis.model_validate(item) for item in state.get("hypotheses", [])
        ]
        ranked = [
            RankedDiagnosis(
                rank=0,
                hypothesis_id=item.hypothesis_id,
                disease_name=item.disease_name,
                evidence_strength=ConfidenceLevel.NOT_ASSESSABLE,
                supporting_evidence=[],
                contradicting_evidence=[],
                medical_knowledge_evidence=[],
                missing_information=item.missing_information,
                clinical_reasoning=item.reasoning_summary,
                symptom_quotes=item.llm_supporting_quotes,
            )
            for item in hypotheses
        ]
        conclusion = (
            "Ces hypothèses proviennent uniquement du modèle local et des symptômes saisis. "
            "Aucun dossier patient, résultat d'examen ou référentiel médical n'a été consulté ; "
            "leur solidité clinique n'est pas évaluable sans examen professionnel."
        )
        assessment = FinalAssessment(
            investigation_id=state["investigation_id"],
            patient_id=state["patient_id"],
            investigation_status="completed_llm_only_unverified",
            differential_diagnoses=ranked,
            conclusion=conclusion,
            comparison_status="not_attempted",
            comparison_error=(
                "ComparisonUnavailable: mode LLM seul sans preuve documentaire."
            ),
            technical_warnings=state.get("technical_warnings", []),
        )
        return {
            "final_assessment": assessment.model_dump(mode="json"),
            "investigation_history": _history(
                state,
                "rank_llm_only_differential",
                f"Restitution non départagée de {len(ranked)} hypothèse(s) LLM non sourcée(s).",
            ),
        }

    def extract_clinical_evidence(self, state: ClinicalInvestigationState) -> dict[str, object]:
        chunks = [DocumentChunk.model_validate(item) for item in state["retrieved_evidence"]]
        facts = extract_evidence_from_chunks(chunks)
        raw = verify_raw_evidence(
            extract_raw_evidence_from_chunks(chunks), self.repository.get_chunk
        )
        processed = sorted({chunk.document_id for chunk in chunks})
        return {
            "extracted_clinical_facts": [item.model_dump(mode="json") for item in facts],
            "raw_clinical_evidence": [item.model_dump(mode="json") for item in raw],
            "retrieved_chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "evidence_history": [
                {
                    "source": "initial_inventory",
                    "chunk_ids": [chunk.chunk_id for chunk in chunks],
                    "raw_evidence_ids": [item.raw_evidence_id for item in raw],
                    "evidence_ids": [item.evidence_id for item in facts],
                }
            ],
            "evidence_revision": len(facts),
            "processed_document_ids": processed,
            "investigation_history": _history(
                state,
                "extract_clinical_evidence",
                f"{len(facts)} indice(s) clinique(s) extrait(s) avec citations.",
                new_evidence_ids=[item.evidence_id for item in facts],
            ),
        }

    def control_document_coverage(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        inventory_ids = [str(item["document_id"]) for item in state["document_inventory"]]
        processed = list(dict.fromkeys(state.get("processed_document_ids", [])))
        facts = [
            PatientEvidence.model_validate(item)
            for item in state.get("extracted_clinical_facts", [])
        ]
        verified_facts = verify_patient_evidence(facts, self.repository.get_chunk)
        rejected = len(facts) - len(verified_facts)
        facts = verified_facts
        laboratory_documents = sorted(
            {
                item.document_id
                for item in facts
                if item.type == "laboratory_result"
                or item.numeric_observations
            }
        )
        missing_documents = sorted(set(inventory_ids) - set(processed))
        laboratory_processed = sorted(set(laboratory_documents) & set(processed))
        gaps = [f"Document non traité : {item}" for item in missing_documents]
        report = CoverageReport(
            inventory_document_ids=inventory_ids,
            processed_document_ids=processed,
            documents_total=len(inventory_ids),
            documents_processed=len(set(processed) & set(inventory_ids)),
            laboratory_documents=laboratory_documents,
            laboratory_documents_processed=laboratory_processed,
            passages_total=len(self.repository.list_chunks(patient_id=state["patient_id"])),
            passages_examined=len({
                str(item.get("chunk_id")) for item in state.get("retrieved_evidence", [])
            }),
            raw_evidence_count=len(state.get("raw_clinical_evidence", [])),
            structured_evidence_count=len(verified_facts),
            complete=not missing_documents
            and set(laboratory_documents) <= set(laboratory_processed)
            and len(processed) >= len(inventory_ids)
            and len({str(item.get("chunk_id")) for item in state.get("retrieved_evidence", [])})
            == len(self.repository.list_chunks(patient_id=state["patient_id"]))
            and len(state.get("raw_clinical_evidence", []))
            >= len(state.get("retrieved_evidence", [])),
            gaps=gaps,
        )
        return {
            "extracted_clinical_facts": [
                item.model_dump(mode="json") for item in verified_facts
            ],
            "coverage_report": report.model_dump(mode="json"),
            "investigation_history": _history(
                state,
                "control_document_coverage",
                f"Couverture : {report.documents_processed}/{report.documents_total} document(s), "
                f"{len(report.laboratory_documents_processed)}/{len(report.laboratory_documents)} "
                f"document(s) biologique(s) traité(s) ; complète={report.complete}.",
                new_evidence_ids=(
                    [] if rejected == 0 else [f"{rejected}_citation(s)_rejetée(s)"]
                ),
            ),
        }

    def identify_discriminative_evidence(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        if self.knowledge is None:
            raise RuntimeError("Le référentiel legacy n'est pas initialisé.")
        facts = [
            PatientEvidence.model_validate(item)
            for item in state.get("extracted_clinical_facts", [])
        ]
        findings = identify_discriminative_findings(facts, self.knowledge.profiles)
        counts = {
            category.value: sum(item.category == category for item in findings)
            for category in DiscriminativeCategory
        }
        return {
            "discriminative_evidence": [item.model_dump(mode="json") for item in findings],
            "investigation_history": _history(
                state,
                "identify_discriminative_evidence",
                f"{len(findings)} indice(s) classé(s) par valeur discriminante : {counts}. "
                "Aucune sensibilité, spécificité ni pondération symptomatique n'a été inventée.",
            ),
        }

    def generate_qwen_grounded_differential(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        facts = [
            PatientEvidence.model_validate(item)
            for item in state.get("extracted_clinical_facts", [])
        ]
        calls = state.get("llm_call_count", 0)
        query = ". ".join(
            [state["consultation_symptoms"], *[item.description for item in facts]]
        )
        profiles = self.knowledge.search(query, limit=8) if self.knowledge is not None else []
        existing_names = [str(item.get("disease_name", "")) for item in state.get("hypotheses", [])]
        hypotheses: list[DiagnosticHypothesis] = []
        knowledge_evidence = [
            MedicalKnowledgeEvidence.model_validate(item)
            for item in state.get("medical_knowledge_evidence", [])
        ]
        status = "Qwen 3.5 local indisponible ou budget LLM épuisé."
        technical_warning: str | None = None
        if (
            self.local_llm is not None
            and self.local_llm.enabled
            and calls < state.get("max_llm_calls", 0)
        ):
            calls += 1
            try:
                candidates = self.local_llm.generate_grounded_differential(
                    state["consultation_symptoms"],
                    facts,
                    state.get("document_inventory", []),
                    profiles,
                    existing_names,
                )
                hypotheses = [
                    DiagnosticHypothesis(
                        hypothesis_id=_hypothesis_id(item.disease_name),
                        disease_name=item.disease_name,
                        concept_type=item.concept_type,
                        reasoning_summary=(
                            "Hypothèse générée; évaluation documentaire structurée en attente."
                        ),
                        generation_rationale=item.reasoning_summary,
                        targeted_search_terms=item.targeted_search_terms,
                        knowledge_limitations=(
                            ["Les fiches médicales locales ont une couverture limitée."]
                            if self.knowledge is None
                            else []
                        ),
                    )
                    for item in candidates
                    if item.disease_name not in existing_names
                ]
                knowledge_ids = {item.knowledge_id for item in knowledge_evidence}
                for hypothesis in hypotheses:
                    profile = (
                        self.knowledge.match_profile(hypothesis.disease_name)
                        if self.knowledge is not None
                        else None
                    )
                    if profile is not None:
                        hypothesis.knowledge_evidence_ids = [profile.knowledge_id]
                        if profile.knowledge_id not in knowledge_ids:
                            knowledge_evidence.append(self.knowledge.to_evidence(profile))
                            knowledge_ids.add(profile.knowledge_id)
                    else:
                        hypothesis.knowledge_limitations = [
                            "Aucune fiche médicale indépendante ne correspond exactement à "
                            "cette hypothèse; ses relations resteront non vérifiables."
                        ]
                status = (
                    f"{len(hypotheses)} hypothèse(s) générée(s) par "
                    f"{self.local_llm.model_identifier} à partir du dossier patient complet."
                )
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                LOGGER.warning(
                    "qwen_grounded_differential_rejected",
                    extra={
                        "event": "qwen_grounded_differential_rejected",
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:300],
                    },
                )
                status = f"Sortie Qwen rejetée ({type(exc).__name__}); aucune hypothèse inventée."
                technical_warning = (
                    f"generate_qwen_grounded_differential:{type(exc).__name__}: "
                    "sortie rejetée; aucune hypothèse substituée."
                )
        else:
            technical_warning = (
                "generate_qwen_grounded_differential:UnavailableOrBudgetExceeded: "
                "aucune hypothèse générée."
            )
        return {
            "hypotheses": [
                *state.get("hypotheses", []),
                *[item.model_dump(mode="json") for item in hypotheses],
            ],
            "llm_call_count": calls,
            "technical_warnings": [
                *state.get("technical_warnings", []),
                *([technical_warning] if technical_warning else []),
            ],
            "hypothesis_generation_round": state.get("hypothesis_generation_round", 0) + 1,
            "last_generated_evidence_revision": state.get("evidence_revision", 0),
            "medical_knowledge_evidence": [
                item.model_dump(mode="json") for item in knowledge_evidence
            ],
            "stop_reason": None if hypotheses else "Qwen n'a produit aucun différentiel valide.",
            "investigation_history": _history(
                state, "generate_qwen_grounded_differential", status
            ),
        }

    def select_qwen_hypothesis(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        hypotheses = [
            DiagnosticHypothesis.model_validate(item) for item in state.get("hypotheses", [])
        ]
        revision = state.get("evidence_revision", 0)
        candidates = [
            item for item in hypotheses
            if item.review_count < 2
            and (
                item.investigation_status != InvestigationStatus.INVESTIGATED
                or item.evidence_revision < revision
            )
        ]
        selected = candidates[0] if candidates else None
        iteration = state.get("iteration_count", 0) + (1 if selected else 0)
        return {
            "selected_hypothesis_id": selected.hypothesis_id if selected else None,
            "iteration_count": iteration,
            "investigation_history": _history(
                {**state, "iteration_count": iteration},
                "select_qwen_hypothesis",
                f"Hypothèse Qwen sélectionnée pour revue contradictoire : {selected.disease_name}."
                if selected
                else "Aucune hypothèse Qwen restante.",
                hypothesis_id=selected.hypothesis_id if selected else None,
            ),
        }

    def retrieve_qwen_targeted_evidence(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        selected_id = state["selected_hypothesis_id"]
        hypotheses = [
            DiagnosticHypothesis.model_validate(item) for item in state.get("hypotheses", [])
        ]
        selected = next(item for item in hypotheses if item.hypothesis_id == selected_id)
        queries = selected.targeted_search_terms or [selected.disease_name]
        profile = self.knowledge.match_profile(selected.disease_name) if self.knowledge else None
        if profile is not None:
            queries = list(dict.fromkeys([
                *queries,
                *profile.typical_manifestations[:5],
                *profile.atypical_manifestations,
                *profile.discriminating_features,
                *profile.useful_tests,
                *profile.contradiction_markers,
                *profile.differential_diagnoses,
            ]))
        chunks, executed = self.patient_retriever.semantic_patient_search(
            state["patient_id"], queries
        )
        chunks = [chunk for chunk in chunks if chunk.patient_id == state["patient_id"]]
        prior_chunks = {
            str(item.get("chunk_id")): item
            for item in [*state.get("retrieved_evidence", []), *state.get("retrieved_chunks", [])]
        }
        merged_chunks = dict(prior_chunks)
        new_chunks: list[DocumentChunk] = []
        for chunk in chunks:
            if chunk.chunk_id not in merged_chunks:
                new_chunks.append(chunk)
            merged_chunks[chunk.chunk_id] = chunk.model_dump(mode="json")
        existing_facts = [
            PatientEvidence.model_validate(item)
            for item in state.get("extracted_clinical_facts", [])
        ]
        candidates = extract_evidence_from_chunks(new_chunks)
        candidates = [
            item
            for item in candidates
            if not evidence_semantically_duplicate(item, existing_facts)
        ]
        verified_new = verify_patient_evidence(candidates, self.repository.get_chunk)
        existing_facts.extend(verified_new)
        raw_existing = [
            item
            for item in state.get("raw_clinical_evidence", [])
            if isinstance(item, dict)
        ]
        raw_ids = {str(item.get("raw_evidence_id")) for item in raw_existing}
        raw_candidates = extract_raw_evidence_from_chunks(new_chunks)
        raw_verified = verify_raw_evidence(
            [item for item in raw_candidates if item.raw_evidence_id not in raw_ids],
            self.repository.get_chunk,
        )
        raw_existing.extend(item.model_dump(mode="json") for item in raw_verified)
        # The full corpus was already extracted; semantic retrieval only focuses the review.
        tracked = [
            *state.get("targeted_queries", []),
            *[f"{selected_id}|{query}" for query in executed],
        ]
        return {
            "retrieved_chunks": list(merged_chunks.values()),
            "retrieved_evidence": list(merged_chunks.values()),
            "raw_clinical_evidence": raw_existing,
            "extracted_clinical_facts": [item.model_dump(mode="json") for item in existing_facts],
            "new_evidence": [item.model_dump(mode="json") for item in verified_new],
            "evidence_history": [
                *state.get("evidence_history", []),
                {
                    "hypothesis_id": selected_id,
                    "query": executed,
                    "chunk_ids": [item.chunk_id for item in new_chunks],
                    "evidence_ids": [item.evidence_id for item in verified_new],
                    "raw_evidence_ids": [item.raw_evidence_id for item in raw_verified],
                },
            ],
            "evidence_revision": state.get("evidence_revision", 0) + (1 if verified_new else 0),
            "targeted_queries": list(dict.fromkeys(tracked)),
            "last_new_evidence_count": len(verified_new),
            "investigation_history": _history(
                state,
                "retrieve_qwen_targeted_evidence",
                f"Recherche Qdrant ciblée dans le seul dossier patient : {len(chunks)} passage(s).",
                hypothesis_id=selected_id,
                query=" | ".join(executed),
            ),
        }

    def review_qwen_hypothesis(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        selected_id = state["selected_hypothesis_id"]
        hypotheses = [
            DiagnosticHypothesis.model_validate(item) for item in state.get("hypotheses", [])
        ]
        selected = next(item for item in hypotheses if item.hypothesis_id == selected_id)
        facts = [
            PatientEvidence.model_validate(item)
            for item in state.get("extracted_clinical_facts", [])
        ]
        profile = self.knowledge.match_profile(selected.disease_name) if self.knowledge else None
        # The full patient corpus is already extracted and source-verified. Relation
        # classification is deterministic, so no per-hypothesis LLM call is necessary.
        coverage = CoverageReport.model_validate(state.get("coverage_report", {}))
        selected = _apply_validated_documentary_review(
            selected,
            facts,
            profile,
            include_undocumented_tests=coverage.complete,
        )
        selected.review_count += 1
        selected.evidence_revision = state.get("evidence_revision", 0)
        updated = [selected if item.hypothesis_id == selected_id else item for item in hypotheses]
        investigated = list(
            dict.fromkeys([*state.get("investigated_hypotheses", []), selected_id])
        )
        return {
            "hypotheses": [item.model_dump(mode="json") for item in updated],
            "investigated_hypotheses": investigated,
            "llm_call_count": state.get("llm_call_count", 0),
            "rejected_evidence_ids": list(state.get("rejected_evidence_ids", [])),
            "investigation_history": _history(
                state,
                "review_qwen_hypothesis",
                f"Validation déterministe : {len(selected.supporting_evidence_ids)} "
                f"favorable(s), {len(selected.contradicting_evidence_ids)} "
                f"défavorable(s), "
                f"{len(relation_ids(selected.evidence_relations, EvidenceRelation.MISSING))} "
                "information(s) manquante(s).",
                hypothesis_id=selected_id,
                new_evidence_ids=[
                    *selected.supporting_evidence_ids,
                    *selected.contradicting_evidence_ids,
                ],
            ),
        }

    def qwen_should_continue(self, state: ClinicalInvestigationState) -> dict[str, object]:
        hypotheses = [
            DiagnosticHypothesis.model_validate(item) for item in state.get("hypotheses", [])
        ]
        revision = state.get("evidence_revision", 0)
        remaining = [
            item for item in hypotheses
            if item.review_count < 2
            and (
                item.investigation_status != InvestigationStatus.INVESTIGATED
                or item.evidence_revision < revision
            )
        ]
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(state["started_at"])).total_seconds()
        reason: str | None = None
        if not hypotheses:
            reason = state.get("stop_reason") or "Aucune hypothèse Qwen valide."
        elif state.get("iteration_count", 0) >= state.get("max_iterations", 0):
            reason = "Budget maximal d'itérations atteint."
        elif elapsed >= state.get("timeout_seconds", 0):
            reason = "Délai maximal d'exécution atteint."
        elif not remaining and (
            revision > state.get("last_generated_evidence_revision", revision)
            and state.get("hypothesis_generation_round", 0) < 2
            and state.get("llm_call_count", 0) < max(0, state.get("max_llm_calls", 0) - 1)
        ):
            reason = "Nouvelle direction clinique justifiÃ©e par des preuves rÃ©centes."
        elif not remaining:
            reason = "Toutes les hypothèses Qwen ont reçu une revue contradictoire."
        return {
            "stop_reason": reason,
            "new_clinical_direction": bool(
                not remaining
                and revision > state.get("last_generated_evidence_revision", revision)
                and state.get("hypothesis_generation_round", 0) < 2
                and state.get("llm_call_count", 0) < max(0, state.get("max_llm_calls", 0) - 1)
                and state.get("iteration_count", 0) < state.get("max_iterations", 0)
                and elapsed < state.get("timeout_seconds", 0)
            ),
            "investigation_history": _history(
                state,
                "qwen_should_continue",
                reason or f"Poursuite : {len(remaining)} hypothèse(s) Qwen restante(s).",
            ),
        }

    def rank_qwen_differential(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        facts_list = [
            PatientEvidence.model_validate(raw)
            for raw in state.get("extracted_clinical_facts", [])
        ]
        facts = {item.evidence_id: item for item in facts_list}
        hypotheses = [
            DiagnosticHypothesis.model_validate(item)
            for item in state.get("hypotheses", [])
        ]

        coverage = CoverageReport.model_validate(state.get("coverage_report", {}))
        # Validate every hypothesis against the already inventoried full corpus. This is
        # deterministic and therefore must not depend on the per-run LLM call budget.
        for hypothesis in hypotheses:
            profile = (
                self.knowledge.match_profile(hypothesis.disease_name)
                if self.knowledge is not None
                else None
            )
            _apply_validated_documentary_review(
                hypothesis,
                facts_list,
                profile,
                include_undocumented_tests=coverage.complete,
            )

        reviews = [
            QwenHypothesisReview(
                hypothesis_id=item.hypothesis_id,
                disease_name=item.disease_name,
                evidence_strength=item.confidence_level,
                supporting_evidence_ids=item.supporting_evidence_ids,
                contradicting_evidence_ids=item.contradicting_evidence_ids,
                discriminative_evidence_ids=item.discriminative_evidence_ids,
                missing_information=item.missing_information,
                chronology_assessed=item.chronology_assessed,
                reasoning_summary=(
                    f"Relations validées: {len(item.supporting_evidence_ids)} favorables, "
                    f"{len(item.contradicting_evidence_ids)} défavorables et "
                    f"{len(item.missing_information)} informations manquantes."
                ),
                validated_relations=[
                    relation.model_dump(mode="json")
                    for relation in item.evidence_relations
                ],
                validated_diagnostic_status=item.diagnostic_status,
                validated_evidence_strength=item.evidence_strength_class,
                validated_uncertainty=item.uncertainty,
            )
            for item in hypotheses
        ]
        calls = state.get("llm_call_count", 0)
        comparison = None
        comparison_error: str | None = None
        comparison_calls = 0
        elapsed_before_comparison = (
            datetime.now(UTC) - datetime.fromisoformat(state["started_at"])
        ).total_seconds()

        timeout_seconds = state.get("timeout_seconds", 0)
        if len(reviews) <= 1:
            comparison_status = "not_required"
        elif timeout_seconds and elapsed_before_comparison >= timeout_seconds:
            comparison_status = "failed"
            comparison_error = "ComparisonTimeout: délai global atteint avant la comparaison."
        elif self.local_llm is None or not self.local_llm.enabled:
            comparison_status = "failed"
            comparison_error = "ComparisonUnavailable: modèle local indisponible."
        elif calls >= state.get("max_llm_calls", 0):
            comparison_status = "failed"
            comparison_error = "ComparisonBudgetExceeded: aucun appel LLM disponible."
        else:
            comparison_status = "failed"
            for attempt in range(1, 3):
                if calls >= state.get("max_llm_calls", 0):
                    if comparison_error is None:
                        comparison_error = (
                            "ComparisonBudgetExceeded: budget épuisé pendant les relances."
                        )
                    break
                calls += 1
                comparison_calls += 1
                try:
                    if isinstance(self.local_llm, LocalGGUFClient):
                        candidate = self.local_llm.compare_grounded_hypotheses(
                            reviews,
                            attempt=attempt,
                            previous_error=comparison_error,
                        )
                    else:
                        candidate = self.local_llm.compare_grounded_hypotheses(reviews)
                    expected_ids = {item.hypothesis_id for item in reviews}
                    ordered_ids = list(candidate.ordered_hypothesis_ids)
                    if not ordered_ids:
                        by_name = {
                            item.disease_name: item.hypothesis_id for item in reviews
                        }
                        ordered_ids = [
                            by_name[name]
                            for name in candidate.ordered_disease_names
                            if name in by_name
                        ]
                    if set(ordered_ids) != expected_ids or len(ordered_ids) != len(reviews):
                        raise ValueError(
                            "Comparison omitted, duplicated, or introduced a hypothesis."
                        )
                    comparison = candidate.model_copy(
                        update={"ordered_hypothesis_ids": ordered_ids}
                    )
                    comparison_status = "completed"
                    comparison_error = None
                    break
                except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                    comparison_error = f"{type(exc).__name__}: {exc}"
                    LOGGER.warning(
                        "qwen_comparison_failed",
                        extra={
                            "event": "qwen_comparison_failed",
                            "attempt": attempt,
                            "error_type": type(exc).__name__,
                            "error_detail": str(exc)[:300],
                            "hypotheses_compared": len(reviews),
                        },
                    )

        proposal_by_id = {
            item.hypothesis_id: item
            for item in (comparison.assessments if comparison is not None else [])
        }
        for hypothesis in hypotheses:
            proposal = proposal_by_id.get(hypothesis.hypothesis_id)
            if proposal is not None:
                hypothesis.alternative_diagnoses = proposal.alternative_diagnoses
                hypothesis.comparative_justification = proposal.comparative_justification
                _validate_documentary_status(
                    hypothesis,
                    proposal.diagnostic_status,
                    proposal.evidence_strength,
                    proposal.uncertainty,
                )
            else:
                hypothesis.comparative_justification = (
                    "Statut dérivé des relations validées; proposition comparative LLM absente."
                )
                _validate_documentary_status(hypothesis)

        can_prioritize = False
        if comparison is not None:
            order = {
                hypothesis_id: index
                for index, hypothesis_id in enumerate(
                    comparison.ordered_hypothesis_ids
                )
            }
            hypotheses.sort(
                key=lambda item: order.get(item.hypothesis_id, len(order))
            )
            # Independent validation: a source-verified confirmatory relation cannot
            # be ranked below hypotheses supported only by compatible manifestations.
            hypotheses.sort(
                key=lambda item: (
                    item.diagnostic_status != DiagnosticStatus.DOCUMENTED_CONFIRMED,
                    order.get(item.hypothesis_id, len(order)),
                )
            )
            confirmed = [
                item for item in hypotheses
                if item.diagnostic_status == DiagnosticStatus.DOCUMENTED_CONFIRMED
            ]
            can_prioritize = bool(comparison.can_prioritize_one or len(confirmed) == 1)
        elif len(hypotheses) == 1:
            can_prioritize = (
                hypotheses[0].diagnostic_status
                != DiagnosticStatus.INSUFFICIENT_EVIDENCE
            )

        ranked: list[RankedDiagnosis] = []
        for index, item in enumerate(hypotheses, start=1):
            relations = item.evidence_relations
            support_count = sum(
                relation.relation == EvidenceRelation.SUPPORTS
                for relation in relations
            )
            contradiction_count = sum(
                relation.relation == EvidenceRelation.CONTRADICTS
                for relation in relations
            )
            neutral_count = sum(
                relation.relation == EvidenceRelation.NEUTRAL
                for relation in relations
            )
            conflict_count = sum(
                relation.relation == EvidenceRelation.DOCUMENT_CONFLICT
                for relation in relations
            )
            unverifiable_count = sum(
                relation.validation_status == RelationValidationStatus.UNVERIFIABLE
                for relation in relations
            )
            cited_ids = {
                evidence_id
                for relation in relations
                for evidence_id in [relation.evidence_id, *relation.related_evidence_ids]
                if evidence_id in facts
            }
            cited = [facts[evidence_id] for evidence_id in sorted(cited_ids)]
            source_references = list(
                dict.fromkeys(
                    f"patient:{source.evidence_id}:{occurrence.filename}:"
                    f"{occurrence.date.isoformat() if occurrence.date else 'date_non_documentée'}"
                    for source in cited
                    for occurrence in (
                        source.source_occurrences
                        or []
                    )
                )
            )
            # Compatibility with old persisted evidence that predates source_occurrences.
            if not source_references:
                source_references = [
                    f"patient:{source.evidence_id}:{source.filename}:"
                    f"{source.date.isoformat() if source.date else 'date_non_documentée'}"
                    for source in cited
                ]
            if conflict_count:
                coherence = "conflicting_documents"
            elif support_count and contradiction_count:
                coherence = "mixed_support_and_contradiction"
            elif relations:
                coherence = "coherent_documentary_relations"
            else:
                coherence = "unknown"
            coverage_label = (
                "complete"
                if coverage.complete
                else "partial"
                if coverage.passages_examined
                else "not_assessed"
            )
            qualitative_basis = (
                f"{support_count} relation(s) favorable(s), "
                f"{contradiction_count} défavorable(s), {neutral_count} non "
                f"discriminante(s), {conflict_count} conflit(s) documentaire(s), "
                f"{len(item.missing_information)} information(s) manquante(s)."
            )
            documentary = DocumentaryAssessment(
                documentary_coverage=coverage_label,
                passages_examined=coverage.passages_examined,
                passages_total=coverage.passages_total,
                verified_source_count=len(cited),
                validated_support_count=support_count,
                validated_contradiction_count=contradiction_count,
                non_discriminating_count=neutral_count,
                document_conflict_count=conflict_count,
                unverifiable_count=unverifiable_count,
                missing_discriminating_information=item.missing_information,
                observation_coherence=coherence,
                validation_status=item.confidence_level,
                qualitative_basis=qualitative_basis,
                coverage_rate=(
                    round(100 * coverage.passages_examined / coverage.passages_total)
                    if coverage.passages_total
                    else 0
                ),
            )
            coverage_rate = documentary.coverage_rate
            supporting_relations = [
                relation for relation in relations
                if relation.relation == EvidenceRelation.SUPPORTS
                and relation.validation_status == RelationValidationStatus.VALIDATED
            ]
            supporting_relations.sort(key=lambda relation: {
                EvidenceTier.CONFIRMATORY: 0,
                EvidenceTier.HIGHLY_DISCRIMINATING: 1,
                EvidenceTier.COMPATIBLE_NON_SPECIFIC: 2,
            }.get(relation.evidence_tier, 3))
            supporting_details = [
                _ranked_evidence(relation, facts) for relation in supporting_relations
            ]
            contradicting_details = [
                _ranked_evidence(relation, facts)
                for relation in relations
                if relation.relation in {
                    EvidenceRelation.CONTRADICTS,
                    EvidenceRelation.DOCUMENT_CONFLICT,
                }
                and relation.validation_status == RelationValidationStatus.VALIDATED
            ]
            proposal = proposal_by_id.get(item.hypothesis_id)
            proposed_position = proposal.relative_position if proposal is not None else 0
            if can_prioritize:
                if item.diagnostic_status == DiagnosticStatus.DOCUMENTED_CONFIRMED:
                    display_rank = 1
                elif proposed_position > 0:
                    display_rank = max(2 if hypotheses[0].diagnostic_status == DiagnosticStatus.DOCUMENTED_CONFIRMED else 1, proposed_position)
                else:
                    display_rank = index
            else:
                # Preserve Qwen's explicitly comparative ordering even when the
                # evidence does not justify naming a single leading diagnosis.
                # Equal positions represent an unresolved tie; zero means Qwen
                # supplied no defensible comparative position.
                display_rank = proposed_position
            ranked.append(
                RankedDiagnosis(
                    rank=display_rank,
                    hypothesis_id=item.hypothesis_id,
                    disease_name=item.disease_name,
                    evidence_strength=item.confidence_level,
                    supporting_evidence=item.supporting_evidence_ids,
                    contradicting_evidence=item.contradicting_evidence_ids,
                    medical_knowledge_evidence=item.knowledge_evidence_ids,
                    missing_information=item.missing_information,
                    clinical_reasoning=item.reasoning_summary,
                    reasoning_summary=item.reasoning_summary,
                    assertions=item.assertions,
                    documentary_assessment=documentary,
                    discriminative_findings=[
                        facts[evidence_id].description
                        for evidence_id in item.discriminative_evidence_ids
                        if evidence_id in facts
                    ],
                    source_references=source_references,
                    certainty_index=0,
                    certainty_index_basis=(
                        "Score composite non calculé faute de pondération validée; "
                        "consulter les dimensions documentaires séparées."
                    ),
                    reliability_rate=coverage_rate,
                    reliability_explanation=(
                        "Alias historique de la couverture documentaire; cette valeur n'est "
                        "ni un score d'étayage ni une probabilité diagnostique."
                    ),
                    diagnostic_status=item.diagnostic_status,
                    evidence_strength_class=item.evidence_strength_class,
                    supporting_evidence_details=supporting_details,
                    contradicting_evidence_details=contradicting_details,
                    primary_evidence=(supporting_details[0] if supporting_details else None),
                    comparative_assessment=ComparativeAssessment(
                        relative_position=display_rank,
                        reason=item.comparative_justification,
                        limitations=(proposal.limitations if proposal is not None else []),
                        alternative_diagnoses=item.alternative_diagnoses,
                    ),
                    uncertainty=item.uncertainty,
                )
            )

        if not hypotheses:
            conclusion = "Aucune hypothèse documentaire valide n'a été produite."
        elif comparison_status == "failed":
            conclusion = (
                "Échec technique explicite de la comparaison; les hypothèses restent "
                f"non départagées. Détail: {comparison_error}"
            )
        elif (
            can_prioritize
            and hypotheses[0].diagnostic_status
            == DiagnosticStatus.DOCUMENTED_CONFIRMED
        ):
            conclusion = (
                f"« {hypotheses[0].disease_name} » est confirmé dans les documents par "
                "une preuve confirmatoire vérifiée et sourcée; ce statut documentaire "
                "reste soumis à la revue du professionnel de santé."
            )
        elif can_prioritize:
            conclusion = (
                f"La comparaison qualitative des relations validées place "
                f"« {hypotheses[0].disease_name} » en tête documentaire; ce classement "
                "ne constitue pas une probabilité ni un diagnostic."
            )
        elif comparison_status == "not_required":
            conclusion = (
                "Une seule hypothèse documentaire est disponible; aucune comparaison "
                "différentielle n'est requise."
            )
        else:
            conclusion = (
                "La comparaison technique est achevée, mais les relations validées ne "
                "permettent pas de départager les hypothèses."
            )

        elapsed = max(
            0.0,
            (datetime.now(UTC) - datetime.fromisoformat(state["started_at"])).total_seconds(),
        )
        assessment = FinalAssessment(
            investigation_id=state["investigation_id"],
            patient_id=state["patient_id"],
            investigation_status="completed_qwen_grounded",
            differential_diagnoses=ranked,
            conclusion=conclusion,
            analysis_reliability_rate=(
                ranked[0].documentary_assessment.coverage_rate if ranked else 0
            ),
            coverage=coverage,
            iteration_count=state.get("iteration_count", 0),
            llm_call_count=calls,
            execution_time_seconds=round(elapsed, 3),
            clinical_findings=[item.model_dump(mode="json") for item in facts.values()],
            comparison_status=comparison_status,
            comparison_method=(
                "qwen_over_validated_relations"
                if comparison_status == "completed"
                else None
            ),
            comparison_error=comparison_error,
            hypotheses_compared=(len(reviews) if comparison_calls else 0),
            evidence_validity={
                "verified": sum(
                    relation.validation_status == RelationValidationStatus.VALIDATED
                    for hypothesis in hypotheses
                    for relation in hypothesis.evidence_relations
                ),
                "rejected": len(set(state.get("rejected_evidence_ids", []))),
                "uncertain": sum(
                    relation.validation_status
                    == RelationValidationStatus.UNVERIFIABLE
                    for hypothesis in hypotheses
                    for relation in hypothesis.evidence_relations
                ),
                "missing": sum(
                    relation.relation == EvidenceRelation.MISSING
                    for hypothesis in hypotheses
                    for relation in hypothesis.evidence_relations
                ),
                "document_conflicts": sum(
                    relation.relation == EvidenceRelation.DOCUMENT_CONFLICT
                    for hypothesis in hypotheses
                    for relation in hypothesis.evidence_relations
                ),
            },
            investigation_completeness=(
                "complete"
                if coverage.complete and comparison_status in {"completed", "not_required"}
                else "partial"
                if coverage.complete
                else "interrupted"
            ),
            technical_warnings=[
                *state.get("technical_warnings", []),
                *(
                    [f"rank_qwen_differential:{comparison_error}"]
                    if comparison_error
                    else []
                ),
            ],
        )
        return {
            "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
            "qwen_comparison": comparison.model_dump(mode="json") if comparison else {},
            "comparison_status": {
                "comparison_status": assessment.comparison_status,
                "comparison_method": assessment.comparison_method,
                "comparison_error": assessment.comparison_error,
                "hypotheses_compared": assessment.hypotheses_compared,
                "comparison_calls": comparison_calls,
            },
            "llm_call_count": calls,
            "final_assessment": assessment.model_dump(mode="json"),
            "investigation_history": _history(
                state,
                "rank_qwen_differential",
                f"Comparaison qualitative de {len(ranked)} hypothèse(s); "
                f"statut={comparison_status}. {conclusion}",
            ),
        }

    def generate_differential_diagnoses(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        parsed = ParsedConsultation.model_validate(state["parsed_consultation"])
        present = [item.name for item in parsed.symptoms if item.status == "present"]
        facts = [PatientEvidence.model_validate(item) for item in state["extracted_clinical_facts"]]
        query = ". ".join([*present, *[item.description for item in facts]])
        profiles = self.knowledge.search(query, limit=8) if present else []
        discriminative = [
            DiscriminativeEvidence.model_validate(item)
            for item in state.get("discriminative_evidence", [])
        ]
        llm_calls = state.get("llm_call_count", 0)
        llm_ranked: set[str] = set()
        llm_status = "ordre déterministe du référentiel"
        if (
            profiles
            and self.local_llm is not None
            and self.local_llm.enabled
            and llm_calls < state.get("max_llm_calls", 0)
        ):
            llm_calls += 1
            try:
                choices = self.local_llm.prioritize_hypotheses(parsed, facts, profiles)
                profile_by_name = {item.disease_name: item for item in profiles}
                ordered_names = [
                    item.disease_name
                    for item in choices
                    if item.disease_name in profile_by_name
                ]
                profiles = [profile_by_name[name] for name in ordered_names] + [
                    item for item in profiles if item.disease_name not in ordered_names
                ]
                llm_ranked = {item.disease_name for item in choices}
                llm_status = (
                    f"priorisation sémantique locale par {self.local_llm.model_identifier} "
                    "limitée aux candidats du référentiel"
                )
            except (RuntimeError, ValueError, KeyError, TypeError):
                llm_status = "sortie GGUF rejetée ; ordre déterministe conservé"
        knowledge_evidence: list[MedicalKnowledgeEvidence] = []
        hypotheses: list[DiagnosticHypothesis] = []
        for profile in profiles:
            matched = [
                symptom
                for symptom in present
                if any(
                    text_match(symptom, manifestation)
                    for manifestation in profile.typical_manifestations
                )
            ]
            evidence = self.knowledge.to_evidence(profile)
            knowledge_evidence.append(evidence)
            hypotheses.append(
                DiagnosticHypothesis(
                    hypothesis_id=_hypothesis_id(profile.disease_name),
                    disease_name=profile.disease_name,
                    knowledge_evidence_ids=[evidence.knowledge_id],
                    missing_information=list(profile.useful_tests),
                    reasoning_summary=(
                        "Hypothèse de travail générée depuis le motif de consultation et le "
                        f"référentiel : {len(matched)} manifestation(s) "
                        "textuellement compatible(s). Priorisation sémantique locale : "
                        f"{'oui' if profile.disease_name in llm_ranked else 'non évaluée'}."
                    ),
                    risk_not_to_miss=profile.risk_not_to_miss,
                    comparison_score=0,
                    discriminative_evidence_ids=[
                        item.finding_id
                        for item in discriminative
                        if profile.disease_name in item.relevant_diagnoses
                    ],
                    knowledge_limitations=[self.knowledge.scope_notice],
                )
            )
        return {
            "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
            "medical_knowledge_evidence": [
                item.model_dump(mode="json") for item in knowledge_evidence
            ],
            "llm_call_count": llm_calls,
            "investigation_history": _history(
                state,
                "generate_differential_diagnoses",
                f"{len(hypotheses)} hypothèse(s) générée(s) depuis un référentiel séparé ; "
                f"{llm_status}.",
            ),
        }

    def select_hypothesis(self, state: ClinicalInvestigationState) -> dict[str, object]:
        hypotheses = [DiagnosticHypothesis.model_validate(item) for item in state["hypotheses"]]
        investigated = set(state.get("investigated_hypotheses", []))
        candidates = [item for item in hypotheses if item.hypothesis_id not in investigated]
        # Selection order only determines what is investigated next. It is deliberately
        # independent from the final ranking and favours information-rich or must-not-miss
        # candidates without treating rarity or vector similarity as clinical probability.
        candidates.sort(
            key=lambda item: (
                not bool(item.discriminative_evidence_ids),
                not item.risk_not_to_miss,
                item.disease_name,
            )
        )
        selected = candidates[0] if candidates else None
        iteration = state.get("iteration_count", 0) + (1 if selected else 0)
        return {
            "selected_hypothesis_id": selected.hypothesis_id if selected else None,
            "iteration_count": iteration,
            "investigation_history": _history(
                {**state, "iteration_count": iteration},
                "select_hypothesis",
                f"Hypothèse sélectionnée : {selected.disease_name}."
                if selected
                else "Aucune hypothèse restante.",
                hypothesis_id=selected.hypothesis_id if selected else None,
            ),
        }

    def retrieve_targeted_evidence(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        selected_id = state.get("selected_hypothesis_id")
        hypotheses = [DiagnosticHypothesis.model_validate(item) for item in state["hypotheses"]]
        selected = next(item for item in hypotheses if item.hypothesis_id == selected_id)
        profile = self.knowledge.get_profile(selected.disease_name)
        chunks, queries = self.patient_retriever.targeted_search(state["patient_id"], profile)
        existing_facts = {
            item.evidence_id: item
            for item in (
                PatientEvidence.model_validate(raw)
                for raw in state.get("extracted_clinical_facts", [])
            )
        }
        extracted = extract_evidence_from_chunks(chunks)
        new_ids = [item.evidence_id for item in extracted if item.evidence_id not in existing_facts]
        for item in extracted:
            existing_facts[item.evidence_id] = item
        tracked_queries = [
            *state.get("targeted_queries", []),
            *[f"{selected.hypothesis_id}|{query}" for query in queries],
        ]
        return {
            "extracted_clinical_facts": [
                item.model_dump(mode="json") for item in existing_facts.values()
            ],
            "targeted_queries": list(dict.fromkeys(tracked_queries)),
            "last_new_evidence_count": len(new_ids),
            "investigation_history": _history(
                state,
                "retrieve_targeted_evidence",
                f"Recherche ciblée : {len(chunks)} passage(s), {len(new_ids)} nouvel indice.",
                hypothesis_id=selected.hypothesis_id,
                query=" | ".join(queries),
                new_evidence_ids=new_ids,
            ),
        }

    def expand_differential_diagnoses(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        """Add newly justified reference candidates, up to eight, after retrieval."""
        hypotheses = [
            DiagnosticHypothesis.model_validate(item) for item in state.get("hypotheses", [])
        ]
        existing = {item.disease_name for item in hypotheses}
        facts = [
            PatientEvidence.model_validate(item)
            for item in state.get("extracted_clinical_facts", [])
        ]
        query = ". ".join(item.description for item in facts)
        added: list[str] = []
        knowledge_evidence = [
            MedicalKnowledgeEvidence.model_validate(item)
            for item in state.get("medical_knowledge_evidence", [])
        ]
        knowledge_ids = {item.knowledge_id for item in knowledge_evidence}
        for profile in self.knowledge.search(query, limit=8) if query else []:
            if profile.disease_name in existing or len(hypotheses) >= 8:
                continue
            evidence = self.knowledge.to_evidence(profile)
            if evidence.knowledge_id not in knowledge_ids:
                knowledge_evidence.append(evidence)
                knowledge_ids.add(evidence.knowledge_id)
            hypothesis = DiagnosticHypothesis(
                hypothesis_id=_hypothesis_id(profile.disease_name),
                disease_name=profile.disease_name,
                knowledge_evidence_ids=[profile.knowledge_id],
                missing_information=list(profile.useful_tests),
                reasoning_summary=(
                    "Hypothèse ajoutée après une découverte documentaire, depuis le "
                    "référentiel médical indépendant."
                ),
                risk_not_to_miss=profile.risk_not_to_miss,
                knowledge_limitations=[self.knowledge.scope_notice],
            )
            hypotheses.append(hypothesis)
            existing.add(profile.disease_name)
            added.append(profile.disease_name)
        return {
            "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
            "medical_knowledge_evidence": [
                item.model_dump(mode="json") for item in knowledge_evidence
            ],
            "investigation_history": _history(
                state,
                "expand_differential_diagnoses",
                f"{len(added)} hypothèse(s) nouvelle(s) justifiée(s) après recherche ciblée."
                if added
                else "Aucune nouvelle hypothèse justifiée par la recherche ciblée.",
                hypothesis_id=state.get("selected_hypothesis_id"),
            ),
        }

    def verify_evidence(self, state: ClinicalInvestigationState) -> dict[str, object]:
        facts = [
            PatientEvidence.model_validate(item)
            for item in state.get("extracted_clinical_facts", [])
        ]
        verified = verify_patient_evidence(facts, self.repository.get_chunk)
        rejected = len(facts) - len(verified)
        return {
            "extracted_clinical_facts": [item.model_dump(mode="json") for item in verified],
            "last_new_evidence_count": max(
                0, state.get("last_new_evidence_count", 0) - rejected
            ),
            "investigation_history": _history(
                state,
                "verify_evidence",
                f"{len(verified)} preuve(s) vérifiée(s), {rejected} citation(s) rejetée(s).",
                hypothesis_id=state.get("selected_hypothesis_id"),
            ),
        }

    def challenge_hypothesis(self, state: ClinicalInvestigationState) -> dict[str, object]:
        selected_id = state["selected_hypothesis_id"]
        hypotheses = [DiagnosticHypothesis.model_validate(item) for item in state["hypotheses"]]
        selected = next(item for item in hypotheses if item.hypothesis_id == selected_id)
        profile = self.knowledge.get_profile(selected.disease_name)
        facts = [PatientEvidence.model_validate(item) for item in state["extracted_clinical_facts"]]
        contradictions = [
            item
            for item in facts
            if item.polarity == EvidencePolarity.NEGATIVE
            and any(
                contradiction_match(marker, item.description)
                for marker in profile.contradiction_markers
            )
        ]
        records = [
            item
            for item in state.get("contradictions", [])
            if item.get("hypothesis_id") != selected_id
        ]
        records.extend(
            {
                "hypothesis_id": selected_id,
                "evidence_id": item.evidence_id,
                "reason": (
                    "Élément explicitement négatif ou normal compatible "
                    "avec un critère contraire."
                ),
            }
            for item in contradictions
        )
        return {
            "contradictions": records,
            "hypotheses": [
                (
                    item.model_copy(update={"contradiction_search_completed": True})
                    if item.hypothesis_id == selected_id
                    else item
                ).model_dump(mode="json")
                for item in hypotheses
            ],
            "investigation_history": _history(
                state,
                "challenge_hypothesis",
                f"{len(contradictions)} élément(s) contradictoire(s) explicite(s) identifié(s).",
                hypothesis_id=selected_id,
                new_evidence_ids=[item.evidence_id for item in contradictions],
            ),
        }

    def update_hypothesis(self, state: ClinicalInvestigationState) -> dict[str, object]:
        selected_id = state["selected_hypothesis_id"]
        hypotheses = [DiagnosticHypothesis.model_validate(item) for item in state["hypotheses"]]
        selected = next(item for item in hypotheses if item.hypothesis_id == selected_id)
        profile = self.knowledge.get_profile(selected.disease_name)
        facts = [
            PatientEvidence.model_validate(item)
            for item in state["extracted_clinical_facts"]
            if item.get("verification_status") == VerificationStatus.VERIFIED
        ]
        contradiction_ids = {
            str(item["evidence_id"])
            for item in state.get("contradictions", [])
            if item.get("hypothesis_id") == selected_id
        }
        discriminative_findings = {
            item.finding_id: item
            for item in (
                DiscriminativeEvidence.model_validate(raw)
                for raw in state.get("discriminative_evidence", [])
            )
            if selected.disease_name in item.relevant_diagnoses
        }
        supporting = {
            item.evidence_id
            for item in facts
            if item.polarity == EvidencePolarity.SUPPORTING
            and any(
                text_match(pattern, item.description)
                for pattern in [
                    *profile.typical_manifestations,
                    *profile.discriminating_features,
                ]
            )
        }
        completed_tests = {
            test
            for test in profile.useful_tests
            if any(
                item.polarity != EvidencePolarity.NEUTRAL
                and text_match(test, item.description)
                for item in facts
            )
        }
        missing = [test for test in profile.useful_tests if test not in completed_tests]
        confirmatory_support = any(
            item.evidence_id in supporting
            and item.type in {"laboratory_result", "imaging"}
            and any(
                text_match(feature, item.description)
                for feature in profile.discriminating_features
            )
            for item in facts
        )
        informative_support = any(
            item.category
            in {
                DiscriminativeCategory.INFORMATIVE_ASSOCIATION,
                DiscriminativeCategory.DISCRIMINATING_LAB,
                DiscriminativeCategory.RELATIVELY_CHARACTERISTIC,
            }
            and item.category != DiscriminativeCategory.EXPLICITLY_INCOMPATIBLE
            for item in discriminative_findings.values()
        )
        if confirmatory_support and not contradiction_ids:
            confidence = ConfidenceLevel.STRONGLY_SUPPORTED
        elif supporting and informative_support and not contradiction_ids:
            confidence = ConfidenceLevel.PLAUSIBLE
        elif supporting or contradiction_ids:
            confidence = ConfidenceLevel.WEAKLY_SUPPORTED
        else:
            confidence = ConfidenceLevel.NOT_ASSESSABLE
        selected.supporting_evidence_ids = sorted(supporting)
        selected.contradicting_evidence_ids = sorted(contradiction_ids)
        selected.missing_information = missing
        selected.confidence_level = confidence
        selected.investigation_status = InvestigationStatus.INVESTIGATED
        selected.discriminative_evidence_ids = sorted(discriminative_findings)
        selected.chronology_assessed = any(item.date is not None for item in facts)
        selected.reasoning_summary = (
            f"Preuves patient favorables vérifiées : {len(supporting)} ; "
            f"{len(contradiction_ids)} élément(s) contraire(s), "
            f"{len(discriminative_findings)} indice(s) discriminant(s) relié(s) au référentiel, "
            f"{len(missing)} information(s) discriminante(s) manquante(s). "
            "La force est une catégorie de preuve documentaire, pas une probabilité diagnostique."
        )
        updated = [selected if item.hypothesis_id == selected_id else item for item in hypotheses]
        investigated = list(dict.fromkeys([*state.get("investigated_hypotheses", []), selected_id]))
        return {
            "hypotheses": [item.model_dump(mode="json") for item in updated],
            "investigated_hypotheses": investigated,
            "missing_information": list(
                dict.fromkeys([*state.get("missing_information", []), *missing])
            ),
            "investigation_history": _history(
                state,
                "update_hypothesis",
                selected.reasoning_summary,
                hypothesis_id=selected_id,
            ),
        }

    def compare_hypotheses(self, state: ClinicalInvestigationState) -> dict[str, object]:
        hypotheses = [DiagnosticHypothesis.model_validate(item) for item in state["hypotheses"]]
        strength_order = {
            ConfidenceLevel.STRONGLY_SUPPORTED: 3,
            ConfidenceLevel.PLAUSIBLE: 2,
            ConfidenceLevel.WEAKLY_SUPPORTED: 1,
            ConfidenceLevel.NOT_ASSESSABLE: 0,
        }

        # Lexicographic clinical safeguards replace the former weighted symptom count.
        # No Qdrant score and no number of generic symptoms is used.
        hypotheses.sort(
            key=lambda item: (
                -strength_order[item.confidence_level],
                item.investigation_status != InvestigationStatus.INVESTIGATED,
                not bool(item.discriminative_evidence_ids),
                bool(item.contradicting_evidence_ids),
                not item.chronology_assessed,
                item.disease_name,
            )
        )
        if hypotheses:
            leader = hypotheses[0]
            for hypothesis in hypotheses:
                if hypothesis.hypothesis_id == leader.hypothesis_id:
                    hypothesis.alternative_comparison = (
                        "Hypothèse la mieux ordonnée selon les catégories de preuve vérifiées."
                    )
                elif hypothesis.confidence_level == leader.confidence_level:
                    hypothesis.alternative_comparison = (
                        f"Niveau de preuve identique à {leader.disease_name}; aucune priorité "
                        "ne peut être déduite du seul nombre de symptômes."
                    )
                else:
                    hypothesis.alternative_comparison = (
                        f"Niveau de preuve documentaire inférieur à {leader.disease_name}."
                    )
        return {
            "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
            "investigation_history": _history(
                state,
                "compare_hypotheses",
                "Comparaison lexicographique par spécificité documentée, statut de "
                "vérification, contradictions, chronologie et lacunes ; aucun score "
                "vectoriel, vote LLM ou compte brut de symptômes n'est utilisé.",
                hypothesis_id=state.get("selected_hypothesis_id"),
            ),
        }

    def should_continue_investigation(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        hypotheses = [DiagnosticHypothesis.model_validate(item) for item in state["hypotheses"]]
        remaining = [
            item
            for item in hypotheses
            if item.hypothesis_id not in set(state.get("investigated_hypotheses", []))
        ]
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(state["started_at"])).total_seconds()
        reason: str | None = None
        if not hypotheses:
            reason = "Aucune hypothèse évaluable n'a pu être générée."
        elif state["iteration_count"] >= state["max_iterations"]:
            reason = "Budget maximal d'itérations atteint."
        elif elapsed >= state["timeout_seconds"]:
            reason = "Délai maximal d'exécution atteint."
        elif not remaining:
            reason = "Toutes les hypothèses prioritaires ont été examinées."
        elif all(
            any(
                query.startswith(f"{hypothesis.hypothesis_id}|")
                for query in state.get("targeted_queries", [])
            )
            for hypothesis in remaining
        ):
            reason = "Les recherches restantes seraient redondantes."
        return {
            "stop_reason": reason,
            "investigation_history": _history(
                state,
                "should_continue_investigation",
                reason or f"Poursuite : {len(remaining)} hypothèse(s) reste(nt) à examiner.",
            ),
        }

    @staticmethod
    def _route_after_decision(state: ClinicalInvestigationState) -> str:
        return "rank" if state.get("stop_reason") else "continue"

    @staticmethod
    def _route_after_generation(state: ClinicalInvestigationState) -> str:
        return "investigate" if state.get("hypotheses") else "decision"

    @staticmethod
    def _route_after_qwen_generation(state: ClinicalInvestigationState) -> str:
        return "investigate" if state.get("hypotheses") else "rank"

    @staticmethod
    def _route_after_document_coverage(state: ClinicalInvestigationState) -> str:
        if state.get("investigation_mode") != "qwen_grounded":
            return "legacy"
        coverage = CoverageReport.model_validate(state.get("coverage_report", {}))
        if coverage.complete:
            return "qwen"
        if state.get("inventory_attempt_count", 0) < 3:
            return "retry_inventory"
        return "rank_partial"

    @staticmethod
    def _route_after_qwen_decision(state: ClinicalInvestigationState) -> str:
        if state.get("new_clinical_direction"):
            return "generate"
        return "rank" if state.get("stop_reason") else "continue"

    def rank_differential_diagnoses(
        self, state: ClinicalInvestigationState
    ) -> dict[str, object]:
        hypotheses = [DiagnosticHypothesis.model_validate(item) for item in state["hypotheses"]]
        strength_order = {
            ConfidenceLevel.STRONGLY_SUPPORTED: 3,
            ConfidenceLevel.PLAUSIBLE: 2,
            ConfidenceLevel.WEAKLY_SUPPORTED: 1,
            ConfidenceLevel.NOT_ASSESSABLE: 0,
        }
        hypotheses.sort(
            key=lambda item: (
                -strength_order[item.confidence_level],
                item.investigation_status != InvestigationStatus.INVESTIGATED,
                not bool(item.discriminative_evidence_ids),
                bool(item.contradicting_evidence_ids),
                not item.chronology_assessed,
                item.disease_name,
            )
        )
        facts = {
            item.evidence_id: item
            for item in (
                PatientEvidence.model_validate(raw)
                for raw in state.get("extracted_clinical_facts", [])
            )
        }
        findings = {
            item.finding_id: item
            for item in (
                DiscriminativeEvidence.model_validate(raw)
                for raw in state.get("discriminative_evidence", [])
            )
        }
        knowledge = {
            item.knowledge_id: item
            for item in (
                MedicalKnowledgeEvidence.model_validate(raw)
                for raw in state.get("medical_knowledge_evidence", [])
            )
        }
        coverage = CoverageReport.model_validate(state.get("coverage_report", {}))
        ranked: list[RankedDiagnosis] = []
        for index, item in enumerate(hypotheses, start=1):
            evidence_ids = list(
                dict.fromkeys(
                    [*item.supporting_evidence_ids, *item.contradicting_evidence_ids]
                )
            )
            patient_sources = [
                facts[evidence_id]
                for evidence_id in evidence_ids
                if evidence_id in facts
            ]
            knowledge_sources = [
                knowledge[knowledge_id]
                for knowledge_id in item.knowledge_evidence_ids
                if knowledge_id in knowledge
            ]
            source_references = [
                (
                    f"patient:{source.evidence_id}:{source.filename}:"
                    f"{source.date.isoformat() if source.date else 'date_non_documentée'}"
                )
                for source in patient_sources
            ] + [
                f"knowledge:{source.knowledge_id}:{source.source_title}:{source.source_url}"
                for source in knowledge_sources
            ]
            discriminative_descriptions = [
                f"[{findings[finding_id].category.value}] "
                f"{findings[finding_id].description}"
                for finding_id in item.discriminative_evidence_ids
                if finding_id in findings
            ]
            reasoning = " ".join(
                part
                for part in (
                    item.reasoning_summary,
                    item.alternative_comparison,
                    (
                        "Limite du référentiel : " + " ".join(item.knowledge_limitations)
                        if item.knowledge_limitations
                        else ""
                    ),
                )
                if part
            )
            coverage_rate = (
                round(100 * coverage.passages_examined / coverage.passages_total)
                if coverage.passages_total
                else 0
            )
            ranked.append(
                RankedDiagnosis(
                    rank=index,
                    hypothesis_id=item.hypothesis_id,
                    disease_name=item.disease_name,
                    evidence_strength=item.confidence_level,
                    supporting_evidence=item.supporting_evidence_ids,
                    contradicting_evidence=item.contradicting_evidence_ids,
                    medical_knowledge_evidence=item.knowledge_evidence_ids,
                    missing_information=item.missing_information,
                    clinical_reasoning=reasoning,
                    reasoning_summary=reasoning,
                    symptom_quotes=item.llm_supporting_quotes,
                    discriminative_findings=discriminative_descriptions,
                    source_references=source_references,
                    documentary_assessment=DocumentaryAssessment(
                        documentary_coverage=(
                            "complete" if coverage.complete else "partial"
                        ),
                        passages_examined=coverage.passages_examined,
                        passages_total=coverage.passages_total,
                        verified_source_count=len(patient_sources),
                        validated_support_count=len(item.supporting_evidence_ids),
                        validated_contradiction_count=len(
                            item.contradicting_evidence_ids
                        ),
                        missing_discriminating_information=item.missing_information,
                        observation_coherence=(
                            "mixed_support_and_contradiction"
                            if item.supporting_evidence_ids
                            and item.contradicting_evidence_ids
                            else "coherent_documentary_relations"
                            if evidence_ids
                            else "unknown"
                        ),
                        validation_status=item.confidence_level,
                        qualitative_basis=(
                            f"{len(item.supporting_evidence_ids)} relation(s) favorable(s), "
                            f"{len(item.contradicting_evidence_ids)} défavorable(s) et "
                            f"{len(item.missing_information)} information(s) manquante(s)."
                        ),
                        coverage_rate=coverage_rate,
                    ),
                    certainty_index=0,
                    certainty_index_basis=(
                        "Score composite non calculé faute de pondération validée."
                    ),
                    reliability_rate=coverage_rate,
                    reliability_explanation=(
                        "Alias historique de la couverture documentaire; cette valeur n'est "
                        "ni un score d'étayage ni une probabilité diagnostique."
                    ),
                )
            )
        all_investigated = all(
            item.investigation_status == InvestigationStatus.INVESTIGATED
            for item in hypotheses
        )
        unique_leader = len(hypotheses) == 1 or (
            len(hypotheses) > 1
            and strength_order[hypotheses[0].confidence_level]
            > strength_order[hypotheses[1].confidence_level]
        )
        reliable = (
            bool(ranked)
            and all_investigated
            and coverage.complete
            and unique_leader
            and ranked[0].evidence_strength
            in {ConfidenceLevel.STRONGLY_SUPPORTED, ConfidenceLevel.PLAUSIBLE}
        )
        conclusion = (
            "Une hypothèse est mieux étayée par les documents, mais elle doit être évaluée "
            "et confirmée par un professionnel de santé."
            if reliable
            else "Aucune hypothèse ne peut être privilégiée avec suffisamment d'éléments."
        )
        elapsed = max(
            0.0,
            (datetime.now(UTC) - datetime.fromisoformat(state["started_at"])).total_seconds(),
        )
        assessment = FinalAssessment(
            investigation_id=state["investigation_id"],
            patient_id=state["patient_id"],
            investigation_status="completed",
            differential_diagnoses=ranked,
            conclusion=conclusion,
            analysis_reliability_rate=(
                ranked[0].documentary_assessment.coverage_rate if ranked else 0
            ),
            coverage=coverage,
            iteration_count=state.get("iteration_count", 0),
            llm_call_count=state.get("llm_call_count", 0),
            execution_time_seconds=round(elapsed, 3),
            comparison_status="completed" if len(ranked) > 1 else "not_required",
            comparison_method="deterministic_qualitative_dimensions",
            hypotheses_compared=len(ranked),
            technical_warnings=state.get("technical_warnings", []),
        )
        return {
            "final_assessment": assessment.model_dump(mode="json"),
            "investigation_history": _history(
                state,
                "rank_differential_diagnoses",
                f"Classement de {len(ranked)} hypothèse(s). {conclusion}",
            ),
        }

    def _compile(self):
        workflow = StateGraph(ClinicalInvestigationState)
        for name in self.NODE_NAMES:
            workflow.add_node(name, getattr(self, name))
        workflow.add_edge(START, "parse_consultation")
        workflow.add_conditional_edges(
            "parse_consultation",
            lambda state: (
                "llm_only"
                if state.get("investigation_mode") == "llm_only"
                else "patient_grounded"
            ),
            {
                "llm_only": "generate_llm_only_differential",
                "patient_grounded": "inventory_patient_documents",
            },
        )
        workflow.add_edge("generate_llm_only_differential", "rank_llm_only_differential")
        workflow.add_edge("rank_llm_only_differential", END)
        workflow.add_edge("inventory_patient_documents", "extract_clinical_evidence")
        workflow.add_edge("extract_clinical_evidence", "control_document_coverage")
        workflow.add_conditional_edges(
            "control_document_coverage",
            self._route_after_document_coverage,
            {
                "qwen": "generate_qwen_grounded_differential",
                "legacy": "identify_discriminative_evidence",
                "retry_inventory": "inventory_patient_documents",
                "rank_partial": "rank_qwen_differential",
            },
        )
        workflow.add_conditional_edges(
            "generate_qwen_grounded_differential",
            self._route_after_qwen_generation,
            {"investigate": "select_qwen_hypothesis", "rank": "rank_qwen_differential"},
        )
        workflow.add_edge("select_qwen_hypothesis", "retrieve_qwen_targeted_evidence")
        workflow.add_edge("retrieve_qwen_targeted_evidence", "review_qwen_hypothesis")
        workflow.add_edge("review_qwen_hypothesis", "qwen_should_continue")
        workflow.add_conditional_edges(
            "qwen_should_continue",
            self._route_after_qwen_decision,
            {
                "continue": "select_qwen_hypothesis",
                "generate": "generate_qwen_grounded_differential",
                "rank": "rank_qwen_differential",
            },
        )
        workflow.add_edge("rank_qwen_differential", END)
        workflow.add_edge("identify_discriminative_evidence", "generate_differential_diagnoses")
        workflow.add_conditional_edges(
            "generate_differential_diagnoses",
            self._route_after_generation,
            {
                "investigate": "select_hypothesis",
                "decision": "should_continue_investigation",
            },
        )
        workflow.add_edge("select_hypothesis", "retrieve_targeted_evidence")
        workflow.add_edge("retrieve_targeted_evidence", "verify_evidence")
        workflow.add_edge("verify_evidence", "expand_differential_diagnoses")
        workflow.add_edge("expand_differential_diagnoses", "challenge_hypothesis")
        workflow.add_edge("challenge_hypothesis", "update_hypothesis")
        workflow.add_edge("update_hypothesis", "compare_hypotheses")
        workflow.add_edge("compare_hypotheses", "should_continue_investigation")
        workflow.add_conditional_edges(
            "should_continue_investigation",
            self._route_after_decision,
            {"continue": "select_hypothesis", "rank": "rank_differential_diagnoses"},
        )
        workflow.add_edge("rank_differential_diagnoses", END)
        return workflow.compile(checkpointer=self.checkpointer)

    def run(
        self,
        patient_id: str,
        consultation_symptoms: str,
        max_iterations: int,
        max_llm_calls: int,
        timeout_seconds: int,
        parent_investigation_id: str | None = None,
    ) -> ClinicalInvestigationState:
        investigation_id = f"inv_{uuid4().hex}"
        initial: ClinicalInvestigationState = {
            "investigation_id": investigation_id,
            "parent_investigation_id": parent_investigation_id,
            "patient_id": patient_id,
            "consultation_symptoms": consultation_symptoms,
            "investigation_mode": self.investigation_mode,
            "document_inventory": [],
            "inventory_attempt_count": 0,
            "processed_document_ids": [],
            "coverage_report": {},
            "extracted_clinical_facts": [],
            "retrieved_evidence": [],
            "medical_knowledge_evidence": [],
            "hypotheses": [],
            "investigated_hypotheses": [],
            "selected_hypothesis_id": None,
            "contradictions": [],
            "discriminative_evidence": [],
            "missing_information": [],
            "iteration_count": 0,
            "max_iterations": max_iterations,
            "llm_call_count": 0,
            "max_llm_calls": max_llm_calls,
            "timeout_seconds": timeout_seconds,
            "started_at": datetime.now(UTC).isoformat(),
            "investigation_history": [],
            "targeted_queries": [],
            "last_new_evidence_count": 0,
            "stop_reason": None,
            "final_assessment": None,
            "qwen_comparison": {},
            "comparison_status": {},
            "technical_warnings": [],
            "rejected_evidence_ids": [],
        }
        print(
            f"[Investigation {investigation_id}] Démarrage du graphe "
            f"(mode={self.investigation_mode}, budget={max_iterations} itération(s)).",
            flush=True,
        )
        LOGGER.info(
            "clinical_investigation_started",
            extra={
                "event": "clinical_investigation_started",
                "investigation_id": investigation_id,
                "mode": self.investigation_mode,
                "max_iterations": max_iterations,
                "max_llm_calls": max_llm_calls,
            },
        )

        result: dict[str, object] = dict(initial)
        config = {
            "configurable": {"thread_id": investigation_id},
            "recursion_limit": max(50, max_iterations * 10),
        }
        for update in self.graph.stream(initial, config, stream_mode="updates"):
            for node, node_update in update.items():
                if not isinstance(node_update, dict):
                    continue
                result.update(node_update)
                self._log_node_progress(investigation_id, node, result, node_update)
        return ClinicalInvestigationState(**result)

    @staticmethod
    def _log_node_progress(
        investigation_id: str,
        node: str,
        state: dict[str, object],
        node_update: dict[str, object],
    ) -> None:
        """Print and persist safe progress metrics without symptom or evidence text."""
        hypotheses = node_update.get("hypotheses", state.get("hypotheses", []))
        evidence = node_update.get(
            "extracted_clinical_facts", state.get("extracted_clinical_facts", [])
        )
        retrieved = node_update.get("retrieved_evidence", state.get("retrieved_evidence", []))
        history = node_update.get("investigation_history", [])
        latest = history[-1] if isinstance(history, list) and history else {}
        if not isinstance(latest, dict):
            latest = {}

        details = {
            "investigation_id": investigation_id,
            "node": node,
            "iteration": int(state.get("iteration_count", 0)),
            "hypothesis_id": latest.get("hypothesis_id")
            or state.get("selected_hypothesis_id"),
            "hypothesis_count": len(hypotheses) if isinstance(hypotheses, list) else 0,
            "clinical_evidence_count": len(evidence) if isinstance(evidence, list) else 0,
            "retrieved_passage_count": len(retrieved) if isinstance(retrieved, list) else 0,
            "new_evidence_count": int(state.get("last_new_evidence_count", 0)),
            "llm_call_count": int(state.get("llm_call_count", 0)),
        }
        if node == "should_continue_investigation":
            details["stop_reason"] = node_update.get("stop_reason") or state.get("stop_reason")

        label = (
            f"[Investigation {investigation_id}] étape={node} "
            f"itération={details['iteration']} hypothèses={details['hypothesis_count']} "
            f"preuves={details['clinical_evidence_count']} "
            f"passages={details['retrieved_passage_count']} "
            f"appels_LLM={details['llm_call_count']}"
        )
        if details["hypothesis_id"]:
            label += f" hypothèse_id={details['hypothesis_id']}"
        if details.get("stop_reason"):
            label += f" arrêt={details['stop_reason']}"
        print(label, flush=True)
        LOGGER.info(
            "clinical_investigation_step",
            extra={"event": "clinical_investigation_step", **details},
        )

    def close(self) -> None:
        self._connection.close()

