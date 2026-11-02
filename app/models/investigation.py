from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as Date
from enum import StrEnum

from pydantic import BaseModel, Field


class SymptomStatus(StrEnum):
    PRESENT = "present"
    DENIED = "denied"
    NOT_DOCUMENTED = "not_documented"


class SymptomObservation(BaseModel):
    name: str
    status: SymptomStatus
    temporal_pattern: str | None = None
    severity: str | None = None
    source_text: str


class ParsedConsultation(BaseModel):
    symptoms: list[SymptomObservation]
    duration: str | None = None
    severity: str | None = None
    missing_details: list[str]
    not_documented: list[str] = Field(default_factory=list)


class EvidenceType(StrEnum):
    SYMPTOM = "symptom"
    HISTORY = "medical_history"
    TREATMENT = "treatment"
    LABORATORY = "laboratory_result"
    IMAGING = "imaging"
    TRIGGER = "trigger"
    FAMILY_HISTORY = "family_history"
    TEMPORAL = "temporal_evolution"
    EXPLICIT_NEGATIVE = "explicit_negative"
    EXAMINATION = "examination"
    MEDICAL_CONCLUSION = "medical_conclusion"
    UNPERFORMED_TEST = "unperformed_test"
    DURATION = "episode_duration"
    OTHER = "other"


class EvidencePolarity(StrEnum):
    SUPPORTING = "present_or_abnormal"
    NEGATIVE = "explicitly_absent_or_normal"
    NEUTRAL = "neutral"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"
    MISSING = "missing"
    UNCERTAIN = "uncertain"
    DOCUMENT_CONFLICT = "document_conflict"
    UNVERIFIABLE = "unverifiable"


class RelationValidationStatus(StrEnum):
    VALIDATED = "validated"
    REJECTED = "rejected"
    UNVERIFIABLE = "unverifiable"


class EvidenceTier(StrEnum):
    CONFIRMATORY = "CONFIRMATORY"
    HIGHLY_DISCRIMINATING = "HIGHLY_DISCRIMINATING"
    COMPATIBLE_NON_SPECIFIC = "COMPATIBLE_NON_SPECIFIC"
    CONTRADICTORY = "CONTRADICTORY"
    NON_DISCRIMINATING = "NON_DISCRIMINATING"
    MISSING = "MISSING"
    UNVERIFIABLE = "UNVERIFIABLE"


class EvidenceSpecificity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class DiagnosticStatus(StrEnum):
    DOCUMENTED_CONFIRMED = "DOCUMENTED_CONFIRMED"
    STRONGLY_SUPPORTED = "STRONGLY_SUPPORTED"
    SUPPORTED = "SUPPORTED"
    POSSIBLE = "POSSIBLE"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceStrengthClass(StrEnum):
    DECISIVE = "DECISIVE"
    HIGHLY_DISCRIMINATING = "HIGHLY_DISCRIMINATING"
    SPECIFIC = "SPECIFIC"
    NON_SPECIFIC = "NON_SPECIFIC"
    CONTRADICTORY = "CONTRADICTORY"
    INSUFFICIENT = "INSUFFICIENT"


class DiagnosticUncertainty(StrEnum):
    LOW_FOR_DOCUMENTED_CONFIRMATION = "LOW_FOR_DOCUMENTED_CONFIRMATION"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    UNDETERMINED = "UNDETERMINED"


class AssertionOrigin(StrEnum):
    PATIENT_FACT = "documented_patient_fact"
    MEDICAL_KNOWLEDGE = "medical_knowledge"
    CLINICAL_INFERENCE = "clinical_inference"
    UNVERIFIED_HYPOTHESIS = "unverified_hypothesis"


class ClinicalConceptType(StrEnum):
    SYMPTOM = "symptom"
    FINDING = "finding"
    SYNDROME = "syndrome"
    ETIOLOGY = "etiology"
    DISEASE = "disease"


class NumericObservation(BaseModel):
    name: str | None = None
    value: float
    unit: str | None = None
    reference_interval: str | None = None


class RawClinicalEvidence(BaseModel):
    raw_evidence_id: str
    patient_id: str
    text: str
    document_id: str
    chunk_id: str
    filename: str
    page: int | None = None
    date: Date | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING


class EvidenceSourceOccurrence(BaseModel):
    document_id: str
    chunk_id: str
    filename: str
    quote: str
    page: int | None = None
    date: Date | None = None


class StructuredClinicalEvidence(BaseModel):
    evidence_id: str
    raw_evidence_id: str | None = None
    patient_id: str
    type: EvidenceType
    concept_type: ClinicalConceptType = ClinicalConceptType.FINDING
    description: str
    quote: str
    document_id: str
    chunk_id: str
    filename: str
    page: int | None = None
    date: Date | None = None
    polarity: EvidencePolarity = EvidencePolarity.NEUTRAL
    numeric_observations: list[NumericObservation] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.PENDING
    source_occurrences: list[EvidenceSourceOccurrence] = Field(default_factory=list)


class PatientEvidence(StructuredClinicalEvidence):
    """Backward-compatible public name for structured patient evidence."""


class EvidenceRelationAssessment(BaseModel):
    relation_id: str = ""
    hypothesis_id: str = ""
    evidence_id: str
    relation: EvidenceRelation
    relevant: bool = False
    temporal_consistent: bool = True
    knowledge_ids: list[str] = Field(default_factory=list)
    patient_observation: str = ""
    document_id: str | None = None
    chunk_id: str | None = None
    criterion: str | None = None
    polarity: EvidencePolarity = EvidencePolarity.NEUTRAL
    validation_status: RelationValidationStatus = RelationValidationStatus.UNVERIFIABLE
    evidence_tier: EvidenceTier = EvidenceTier.UNVERIFIABLE
    specificity: EvidenceSpecificity = EvidenceSpecificity.UNKNOWN
    classification_rationale: str = ""
    related_evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class ValidatedAssertion(BaseModel):
    assertion_id: str
    origin: AssertionOrigin
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    knowledge_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.PENDING


class DiscriminativeCategory(StrEnum):
    NON_SPECIFIC = "frequent_non_specific"
    RELATIVELY_CHARACTERISTIC = "relatively_characteristic"
    INFORMATIVE_ASSOCIATION = "informative_association"
    DISCRIMINATING_LAB = "discriminating_laboratory"
    EXPLICITLY_INCOMPATIBLE = "explicitly_incompatible"


class DiscriminativeEvidence(BaseModel):
    finding_id: str
    category: DiscriminativeCategory
    description: str
    patient_evidence_ids: list[str]
    relevant_diagnoses: list[str] = Field(default_factory=list)
    knowledge_ids: list[str] = Field(default_factory=list)
    rationale: str


class MedicalKnowledgeEvidence(BaseModel):
    knowledge_id: str
    disease_name: str
    statement: str
    evidence_category: str
    source_title: str
    source_url: str
    source_organization: str
    last_reviewed: str
    retrieved_at: Date


class MedicalKnowledgeProfile(BaseModel):
    knowledge_id: str
    disease_name: str
    aliases: list[str]
    typical_manifestations: list[str]
    atypical_manifestations: list[str] = Field(default_factory=list)
    confirmatory_features: list[str] = Field(default_factory=list)
    discriminating_features: list[str]
    useful_tests: list[str]
    differential_diagnoses: list[str]
    contradiction_markers: list[str] = Field(default_factory=list)
    risk_not_to_miss: bool = False
    source_title: str
    source_url: str
    source_organization: str
    last_reviewed: str
    curated_at: Date

    @property
    def searchable_text(self) -> str:
        values = [
            self.disease_name,
            *self.aliases,
            *self.typical_manifestations,
            *self.atypical_manifestations,
            *self.confirmatory_features,
            *self.discriminating_features,
            *self.useful_tests,
            *self.differential_diagnoses,
        ]
        return ". ".join(values)


class InvestigationStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    INVESTIGATED = "investigated"
    NEEDS_CLINICIAN = "needs_clinician_review"


class ConfidenceLevel(StrEnum):
    STRONGLY_SUPPORTED = "fortement étayée"
    PLAUSIBLE = "plausible"
    WEAKLY_SUPPORTED = "faiblement étayée"
    NOT_ASSESSABLE = "non évaluable avec les données disponibles"


class DiagnosticHypothesis(BaseModel):
    hypothesis_id: str
    disease_name: str
    concept_type: ClinicalConceptType = ClinicalConceptType.DISEASE
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    knowledge_evidence_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    investigation_status: InvestigationStatus = InvestigationStatus.PENDING
    confidence_level: ConfidenceLevel = ConfidenceLevel.NOT_ASSESSABLE
    reasoning_summary: str
    generation_rationale: str = ""
    risk_not_to_miss: bool = False
    comparison_score: int = 0
    llm_supporting_quotes: list[str] = Field(default_factory=list)
    discriminative_evidence_ids: list[str] = Field(default_factory=list)
    contradiction_search_completed: bool = False
    chronology_assessed: bool = False
    alternative_comparison: str = ""
    knowledge_limitations: list[str] = Field(default_factory=list)
    targeted_search_terms: list[str] = Field(default_factory=list)
    evidence_relations: list[EvidenceRelationAssessment] = Field(default_factory=list)
    assertions: list[ValidatedAssertion] = Field(default_factory=list)
    review_count: int = 0
    evidence_revision: int = 0
    diagnostic_status: DiagnosticStatus = DiagnosticStatus.INSUFFICIENT_EVIDENCE
    evidence_strength_class: EvidenceStrengthClass = EvidenceStrengthClass.INSUFFICIENT
    uncertainty: DiagnosticUncertainty = DiagnosticUncertainty.UNDETERMINED
    alternative_diagnoses: list[str] = Field(default_factory=list)
    comparative_justification: str = ""


class InvestigationHistoryEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    node: str
    iteration: int
    hypothesis_id: str | None = None
    query: str | None = None
    details: str
    new_evidence_ids: list[str] = Field(default_factory=list)


class DocumentaryAssessment(BaseModel):
    documentary_coverage: str = "not_assessed"
    passages_examined: int = 0
    passages_total: int = 0
    verified_source_count: int = 0
    validated_support_count: int = 0
    validated_contradiction_count: int = 0
    non_discriminating_count: int = 0
    document_conflict_count: int = 0
    unverifiable_count: int = 0
    missing_discriminating_information: list[str] = Field(default_factory=list)
    observation_coherence: str = "unknown"
    validation_status: ConfidenceLevel = ConfidenceLevel.NOT_ASSESSABLE
    qualitative_basis: str = "Aucune relation validée disponible."
    coverage_rate: int = Field(default=0, ge=0, le=100)


class RankedEvidence(BaseModel):
    evidence_id: str
    type: EvidenceTier
    observation: str
    source_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    criterion: str | None = None
    specificity: EvidenceSpecificity = EvidenceSpecificity.UNKNOWN
    validation: RelationValidationStatus = RelationValidationStatus.UNVERIFIABLE
    rationale: str = ""


class ComparativeAssessment(BaseModel):
    relative_position: int = Field(default=0, ge=0)
    reason: str = ""
    limitations: list[str] = Field(default_factory=list)
    alternative_diagnoses: list[str] = Field(default_factory=list)


class RankedDiagnosis(BaseModel):
    rank: int
    hypothesis_id: str
    disease_name: str
    evidence_strength: ConfidenceLevel
    supporting_evidence: list[str]
    contradicting_evidence: list[str]
    medical_knowledge_evidence: list[str]
    missing_information: list[str]
    clinical_reasoning: str
    symptom_quotes: list[str] = Field(default_factory=list)
    discriminative_findings: list[str] = Field(default_factory=list)
    source_references: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    assertions: list[ValidatedAssertion] = Field(default_factory=list)
    documentary_assessment: DocumentaryAssessment = Field(default_factory=DocumentaryAssessment)
    requires_clinician_review: bool = True
    certainty_index: int = Field(default=0, ge=0, le=100)
    certainty_index_basis: str = "Score composite non calculé faute de calibration empirique."
    reliability_rate: int = Field(default=0, ge=0, le=100)
    reliability_explanation: str = (
        "Taux d'étayage documentaire pondéré par la couverture des passages ; "
        "ce taux n'est pas une probabilité diagnostique."
    )
    diagnostic_status: DiagnosticStatus = DiagnosticStatus.INSUFFICIENT_EVIDENCE
    evidence_strength_class: EvidenceStrengthClass = EvidenceStrengthClass.INSUFFICIENT
    supporting_evidence_details: list[RankedEvidence] = Field(default_factory=list)
    contradicting_evidence_details: list[RankedEvidence] = Field(default_factory=list)
    primary_evidence: RankedEvidence | None = None
    comparative_assessment: ComparativeAssessment = Field(
        default_factory=ComparativeAssessment
    )
    uncertainty: DiagnosticUncertainty = DiagnosticUncertainty.UNDETERMINED


class CoverageReport(BaseModel):
    inventory_document_ids: list[str] = Field(default_factory=list)
    processed_document_ids: list[str] = Field(default_factory=list)
    documents_total: int = 0
    documents_processed: int = 0
    laboratory_documents: list[str] = Field(default_factory=list)
    laboratory_documents_processed: list[str] = Field(default_factory=list)
    passages_total: int = 0
    passages_examined: int = 0
    raw_evidence_count: int = 0
    structured_evidence_count: int = 0
    complete: bool = False
    gaps: list[str] = Field(default_factory=list)


class FinalAssessment(BaseModel):
    investigation_id: str
    patient_id: str
    investigation_status: str
    differential_diagnoses: list[RankedDiagnosis]
    conclusion: str
    requires_clinician_review: bool = True
    analysis_reliability_rate: int = Field(default=0, ge=0, le=100)
    reliability_notice: str = (
        "Champ historique conservé pour compatibilité et désormais égal à la couverture "
        "documentaire ; il ne représente ni une force de preuve ni une probabilité."
    )
    certainty_index_notice: str = (
        "Aucun pourcentage diagnostique ou d'étayage n'est calculé. La couverture du corpus "
        "est présentée séparément des statuts et de la hiérarchie des preuves."
    )
    coverage: CoverageReport = Field(default_factory=CoverageReport)
    iteration_count: int = 0
    llm_call_count: int = 0
    execution_time_seconds: float = 0.0
    clinical_findings: list[dict[str, object]] = Field(default_factory=list)
    comparison_status: str = "not_attempted"
    comparison_method: str | None = None
    comparison_error: str | None = None
    hypotheses_compared: int = 0
    evidence_validity: dict[str, int] = Field(default_factory=dict)
    investigation_completeness: str = "non_conclusive"
    technical_warnings: list[str] = Field(default_factory=list)
    safety_notice: str = (
        "Hypothèses documentaires à évaluer par un professionnel de santé ; "
        "aucun diagnostic ni traitement n'est établi par ce prototype."
    )

