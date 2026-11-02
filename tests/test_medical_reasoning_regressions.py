from __future__ import annotations

from datetime import UTC, datetime

from app.agents.local_gguf import (
    QwenComparativeHypothesisAssessment,
    QwenComparativeRanking,
)
from app.investigation.evidence import extract_evidence_from_chunks
from app.investigation.validation import validate_evidence_relations
from app.models.document import DocumentChunk
from app.models.investigation import (
    ConfidenceLevel,
    DiagnosticStatus,
    DiagnosticUncertainty,
    DiagnosticHypothesis,
    EvidenceRelation,
    EvidenceStrengthClass,
    EvidenceTier,
    EvidenceType,
    RelationValidationStatus,
    VerificationStatus,
)


def _chunk(text: str, document_id: str = "doc-a", chunk_id: str = "chunk-a") -> DocumentChunk:
    return DocumentChunk(
        document_id=document_id,
        filename=f"{document_id}.txt",
        page=1,
        chunk_id=chunk_id,
        text=text,
        position=0,
        patient_id="fictional-patient",
    )


def _verified_fact(text: str, *, evidence_type: EvidenceType = EvidenceType.LABORATORY):
    fact = extract_evidence_from_chunks([_chunk(text)])[0]
    fact.type = evidence_type
    fact.verification_status = VerificationStatus.VERIFIED
    return fact


def _rank_state(hypotheses, facts, *, max_llm_calls: int = 2):
    return {
        "investigation_id": "reasoning-regression",
        "patient_id": "fictional-patient",
        "started_at": datetime.now(UTC).isoformat(),
        "timeout_seconds": 60,
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "extracted_clinical_facts": [item.model_dump(mode="json") for item in facts],
        "coverage_report": {
            "documents_total": 2,
            "documents_processed": 2,
            "passages_total": 2,
            "passages_examined": 2,
            "complete": True,
        },
        "llm_call_count": 0,
        "max_llm_calls": max_llm_calls,
        "iteration_count": len(hypotheses),
        "investigation_history": [],
    }


def test_negative_serology_is_a_traced_unfavorable_relation(container) -> None:
    fact = _verified_fact("La sérologie cœliaque est négative dans ce compte rendu.")
    profile = container.knowledge_service.match_profile("Maladie cœliaque")

    relations = validate_evidence_relations(
        [fact], profile, hypothesis_id="hyp-celiac"
    )

    contradiction = next(
        item for item in relations if item.relation == EvidenceRelation.CONTRADICTS
    )
    assert contradiction.relation_id.startswith("rel_")
    assert contradiction.hypothesis_id == "hyp-celiac"
    assert contradiction.evidence_id == fact.evidence_id
    assert contradiction.document_id == fact.document_id
    assert contradiction.criterion == profile.contradiction_markers[0]
    assert contradiction.validation_status == RelationValidationStatus.VALIDATED


def test_laura_nipah_pcr_is_supported_without_confusing_negative_dengue_pcr(
    container,
) -> None:
    nipah = _verified_fact(
        "Détection d'ARN du virus Nipah par RT-PCR spécifique sur un prélèvement "
        "respiratoire, confirmée par une seconde cible moléculaire."
    )
    dengue = _verified_fact(
        "La RT-PCR dengue est négative.",
        evidence_type=EvidenceType.LABORATORY,
    )
    profile = container.knowledge_service.match_profile("Encéphalite à virus Nipah")

    relations = validate_evidence_relations(
        [nipah, dengue], profile, hypothesis_id="hyp-nipah"
    )

    assert any(
        item.evidence_id == nipah.evidence_id
        and item.relation == EvidenceRelation.SUPPORTS
        and item.criterion in profile.discriminating_features
        for item in relations
    )
    assert not any(
        item.evidence_id == dengue.evidence_id
        and item.relation == EvidenceRelation.CONTRADICTS
        for item in relations
    )

    japanese_profile = container.knowledge_service.match_profile(
        "Encéphalite japonaise"
    )
    japanese_relations = validate_evidence_relations(
        [nipah], japanese_profile, hypothesis_id="hyp-japanese-encephalitis"
    )
    assert not any(
        item.evidence_tier == EvidenceTier.CONFIRMATORY
        for item in japanese_relations
    )


def test_imaging_report_heading_is_not_a_disease_specific_finding(container) -> None:
    fact = _verified_fact(
        "# IRM cérébrale à 30/08/2026 16:10",
        evidence_type=EvidenceType.OTHER,
    )
    profile = container.knowledge_service.match_profile("Encéphalite herpétique")

    relations = validate_evidence_relations(
        [fact], profile, hypothesis_id="hyp-hsv", proposed_supporting_ids=[fact.evidence_id]
    )

    assert len(relations) == 1
    assert relations[0].relation == EvidenceRelation.UNVERIFIABLE
    assert relations[0].evidence_tier == EvidenceTier.UNVERIFIABLE


def test_undocumented_endoscopy_stays_missing_and_never_becomes_negative(container) -> None:
    fact = _verified_fact(
        "La recherche de Helicobacter pylori est négative.",
        evidence_type=EvidenceType.LABORATORY,
    )
    profile = container.knowledge_service.match_profile("Ulcère gastroduodénal")

    relations = validate_evidence_relations(
        [fact],
        profile,
        hypothesis_id="hyp-ulcer",
        include_undocumented_tests=True,
    )

    endoscopy = [
        item for item in relations if item.criterion == "endoscopie digestive haute"
    ]
    assert len(endoscopy) == 1
    assert endoscopy[0].relation == EvidenceRelation.MISSING
    assert endoscopy[0].document_id is None
    assert all(
        not (
            item.relation == EvidenceRelation.CONTRADICTS
            and item.criterion == "endoscopie normale"
        )
        for item in relations
    )


def test_unsourced_medical_assertion_is_removed_from_report_narrative(container) -> None:
    hypothesis = DiagnosticHypothesis(
        hypothesis_id="hyp-aip",
        disease_name="Porphyrie aiguë intermittente",
        reasoning_summary="Mutation ALAD supposée par le modèle.",
        generation_rationale="Mutation ALAD supposée par le modèle.",
    )
    fact = _verified_fact(
        "Le porphobilinogène urinaire est augmenté pendant la crise."
    )

    result = container.clinical_graph.rank_qwen_differential(
        _rank_state([hypothesis], [fact])
    )
    diagnosis = result["final_assessment"]["differential_diagnoses"][0]

    assert "ALAD" not in diagnosis["clinical_reasoning"]
    assert "porphobilinogène" in diagnosis["clinical_reasoning"]
    assert {item["origin"] for item in diagnosis["assertions"]} >= {
        "documented_patient_fact",
        "medical_knowledge",
        "clinical_inference",
        "unverified_hypothesis",
    }


def test_concurrent_hypotheses_are_compared_from_validated_relations(container) -> None:
    class Comparator:
        enabled = True
        model_identifier = "comparison-test"

        def compare_grounded_hypotheses(self, reviews):
            return QwenComparativeRanking(
                ordered_hypothesis_ids=[item.hypothesis_id for item in reviews],
                can_prioritize_one=True,
                conclusion="Ordre qualitatif de test.",
            )

    graph = container.clinical_graph
    graph.local_llm = Comparator()
    hypotheses = [
        DiagnosticHypothesis(
            hypothesis_id="hyp-aip",
            disease_name="Porphyrie aiguë intermittente",
            reasoning_summary="En attente de validation.",
        ),
        DiagnosticHypothesis(
            hypothesis_id="hyp-celiac",
            disease_name="Maladie cœliaque",
            reasoning_summary="En attente de validation.",
        ),
    ]
    facts = [
        _verified_fact("Le porphobilinogène urinaire est augmenté pendant la crise."),
        _verified_fact("La sérologie cœliaque est négative dans ce compte rendu."),
    ]

    result = graph.rank_qwen_differential(_rank_state(hypotheses, facts))
    assessment = result["final_assessment"]

    assert assessment["comparison_status"] == "completed"
    assert assessment["hypotheses_compared"] == 2
    assert assessment["differential_diagnoses"][0]["rank"] == 1
    assert assessment["differential_diagnoses"][0]["diagnostic_status"] == (
        DiagnosticStatus.DOCUMENTED_CONFIRMED.value
    )


def test_comparison_budget_failure_is_explicit(container) -> None:
    class Comparator:
        enabled = True
        model_identifier = "comparison-test"

        def compare_grounded_hypotheses(self, reviews):
            raise AssertionError("Le comparateur ne doit pas être appelé sans budget.")

    graph = container.clinical_graph
    graph.local_llm = Comparator()
    hypotheses = [
        DiagnosticHypothesis(
            hypothesis_id=f"hyp-{index}",
            disease_name=f"Hypothèse fictive {index}",
            reasoning_summary="En attente de validation documentaire.",
        )
        for index in range(2)
    ]

    result = graph.rank_qwen_differential(
        _rank_state(hypotheses, [], max_llm_calls=0)
    )
    assessment = result["final_assessment"]

    assert assessment["comparison_status"] == "failed"
    assert assessment["comparison_error"].startswith("ComparisonBudgetExceeded:")
    assert all(item["rank"] == 0 for item in assessment["differential_diagnoses"])
    assert assessment["conclusion"].startswith("Échec technique explicite")


def test_repeated_observation_is_counted_once_but_keeps_all_sources() -> None:
    sentence = "La fatigue apparaît pendant les épisodes douloureux."
    evidence = extract_evidence_from_chunks(
        [
            _chunk(sentence, document_id="doc-a", chunk_id="chunk-a"),
            _chunk(sentence, document_id="doc-b", chunk_id="chunk-b"),
        ]
    )

    assert len(evidence) == 1
    assert {item.document_id for item in evidence[0].source_occurrences} == {
        "doc-a",
        "doc-b",
    }


def test_confirmatory_evidence_outranks_many_non_specific_findings(container) -> None:
    class Comparator:
        enabled = True
        model_identifier = "comparison-test"

        def compare_grounded_hypotheses(self, reviews):
            by_name = {item.disease_name: item for item in reviews}
            hsv = by_name["Encéphalite herpétique"]
            nipah = by_name["Infection par le virus Nipah"]
            return QwenComparativeRanking(
                # Deliberately wrong LLM order: the independent validator must reject it.
                ordered_hypothesis_ids=[hsv.hypothesis_id, nipah.hypothesis_id],
                assessments=[
                    QwenComparativeHypothesisAssessment(
                        hypothesis_id=hsv.hypothesis_id,
                        diagnostic_status=DiagnosticStatus.STRONGLY_SUPPORTED,
                        evidence_strength=EvidenceStrengthClass.HIGHLY_DISCRIMINATING,
                        supporting_evidence_ids=hsv.supporting_evidence_ids,
                        discriminative_evidence_ids=hsv.discriminative_evidence_ids,
                        comparative_justification="Plusieurs symptômes neurologiques compatibles.",
                        uncertainty=DiagnosticUncertainty.MODERATE,
                        relative_position=1,
                    ),
                    QwenComparativeHypothesisAssessment(
                        hypothesis_id=nipah.hypothesis_id,
                        diagnostic_status=DiagnosticStatus.POSSIBLE,
                        evidence_strength=EvidenceStrengthClass.NON_SPECIFIC,
                        supporting_evidence_ids=nipah.supporting_evidence_ids,
                        discriminative_evidence_ids=nipah.discriminative_evidence_ids,
                        comparative_justification="Confirmation virologique citée mais sous-classée.",
                        uncertainty=DiagnosticUncertainty.HIGH,
                        relative_position=2,
                    ),
                ],
                can_prioritize_one=True,
                conclusion="Ordre volontairement incohérent pour tester le validateur.",
            )

    graph = container.clinical_graph
    graph.local_llm = Comparator()
    hypotheses = [
        DiagnosticHypothesis(
            hypothesis_id="hyp-nipah",
            disease_name="Infection par le virus Nipah",
            reasoning_summary="À comparer.",
        ),
        DiagnosticHypothesis(
            hypothesis_id="hyp-hsv",
            disease_name="Encéphalite herpétique",
            reasoning_summary="À comparer.",
        ),
    ]
    facts = [
        _verified_fact(
            "Détection d'ARN du virus Nipah par RT-PCR spécifique sur prélèvement "
            "respiratoire, confirmée par une seconde cible moléculaire."
        ),
        _verified_fact("Fièvre avec confusion.", evidence_type=EvidenceType.SYMPTOM),
        _verified_fact("Céphalées.", evidence_type=EvidenceType.SYMPTOM),
        _verified_fact("Somnolence.", evidence_type=EvidenceType.SYMPTOM),
    ]

    result = graph.rank_qwen_differential(_rank_state(hypotheses, facts))
    diagnoses = result["final_assessment"]["differential_diagnoses"]

    assert diagnoses[0]["disease_name"] == "Infection par le virus Nipah"
    assert diagnoses[0]["diagnostic_status"] == "DOCUMENTED_CONFIRMED"
    assert diagnoses[0]["evidence_strength_class"] == "DECISIVE"
    assert diagnoses[0]["primary_evidence"]["type"] == "CONFIRMATORY"
    assert diagnoses[1]["diagnostic_status"] != "DOCUMENTED_CONFIRMED"


def test_compatible_hypothesis_without_specific_test_stays_possible(container) -> None:
    hypothesis = DiagnosticHypothesis(
        hypothesis_id="hyp-hsv",
        disease_name="Encéphalite herpétique",
        reasoning_summary="À évaluer.",
    )
    facts = [
        _verified_fact("Fièvre avec confusion.", evidence_type=EvidenceType.SYMPTOM),
        _verified_fact("Céphalées.", evidence_type=EvidenceType.SYMPTOM),
    ]

    result = container.clinical_graph.rank_qwen_differential(
        _rank_state([hypothesis], facts)
    )
    diagnosis = result["final_assessment"]["differential_diagnoses"][0]

    assert diagnosis["diagnostic_status"] == "POSSIBLE"
    assert diagnosis["evidence_strength_class"] == "NON_SPECIFIC"
    assert diagnosis["uncertainty"] == "HIGH"
    assert all(
        item["type"] == EvidenceTier.COMPATIBLE_NON_SPECIFIC.value
        for item in diagnosis["supporting_evidence_details"]
    )


def test_repeated_confirmatory_result_is_one_relation_with_all_sources(container) -> None:
    first = _verified_fact(
        "RT-PCR spécifique du virus Nipah positive sur prélèvement respiratoire."
    )
    second = _verified_fact(
        "La RT-PCR spécifique du virus Nipah est positive sur un prélèvement respiratoire."
    )
    second.document_id = "doc-b"
    second.chunk_id = "chunk-b"
    second.filename = "synthese.md"
    profile = container.knowledge_service.match_profile("Infection par le virus Nipah")

    relations = validate_evidence_relations(
        [first, second], profile, hypothesis_id="hyp-nipah"
    )
    confirmatory = [
        item for item in relations
        if item.relation == EvidenceRelation.SUPPORTS
        and item.evidence_tier == EvidenceTier.CONFIRMATORY
    ]

    assert len(confirmatory) == 1
    assert second.evidence_id in confirmatory[0].related_evidence_ids


def test_ranked_json_keeps_status_strength_uncertainty_and_comparison(container) -> None:
    hypothesis = DiagnosticHypothesis(
        hypothesis_id="hyp-nipah",
        disease_name="Infection par le virus Nipah",
        reasoning_summary="À évaluer.",
    )
    fact = _verified_fact(
        "RT-PCR spécifique du virus Nipah positive sur prélèvement respiratoire."
    )

    result = container.clinical_graph.rank_qwen_differential(
        _rank_state([hypothesis], [fact])
    )
    encoded = __import__("json").dumps(result, ensure_ascii=False)
    decoded = __import__("json").loads(encoded)
    diagnosis = decoded["final_assessment"]["differential_diagnoses"][0]

    assert diagnosis["diagnostic_status"] == "DOCUMENTED_CONFIRMED"
    assert diagnosis["evidence_strength_class"] == "DECISIVE"
    assert diagnosis["uncertainty"] == "LOW_FOR_DOCUMENTED_CONFIRMATION"
    assert diagnosis["comparative_assessment"]["relative_position"] == 1
    assert diagnosis["primary_evidence"]["validation"] == "validated"


def test_frontend_does_not_present_documentary_support_percentage() -> None:
    component = (
        __import__("pathlib").Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "components"
        / "InvestigationReport.vue"
    ).read_text(encoding="utf-8")

    assert "supportRate(" not in component
    assert "Étayage documentaire" not in component
    assert "diagnosticStatusLabel" in component


def test_hypothesis_without_validated_relation_is_non_assessable(container) -> None:
    hypothesis = DiagnosticHypothesis(
        hypothesis_id="hyp-unknown",
        disease_name="Étiologie fictive sans référentiel",
        reasoning_summary="Suggestion non vérifiée du modèle.",
    )

    result = container.clinical_graph.rank_qwen_differential(
        _rank_state([hypothesis], [])
    )
    diagnosis = result["final_assessment"]["differential_diagnoses"][0]

    assert diagnosis["evidence_strength"] == ConfidenceLevel.NOT_ASSESSABLE.value
    assert diagnosis["documentary_assessment"]["verified_source_count"] == 0
    assert "non évaluable" in diagnosis["clinical_reasoning"]


def test_narrative_is_coherent_with_structured_contradictions(container) -> None:
    hypothesis = DiagnosticHypothesis(
        hypothesis_id="hyp-celiac",
        disease_name="Maladie cœliaque",
        reasoning_summary="Le modèle affirme à tort qu'il n'existe aucune contradiction.",
    )
    fact = _verified_fact("La sérologie cœliaque est négative dans ce compte rendu.")

    result = container.clinical_graph.rank_qwen_differential(
        _rank_state([hypothesis], [fact])
    )
    diagnosis = result["final_assessment"]["differential_diagnoses"][0]

    assert diagnosis["contradicting_evidence"] == [fact.evidence_id]
    assert "Éléments défavorables validés" in diagnosis["clinical_reasoning"]
    assert "aucune contradiction" not in diagnosis["clinical_reasoning"].lower()


def test_structured_extraction_fallback_is_exposed_as_technical_warning(container) -> None:
    class InvalidExtraction:
        enabled = True
        model_identifier = "invalid-extraction-test"

        def parse_consultation(self, text, deterministic_fallback):
            raise ValueError("invalid structured output")

    graph = container.clinical_graph
    graph.local_llm = InvalidExtraction()

    update = graph.parse_consultation(
        {
            "consultation_symptoms": "Fatigue persistante sans autre précision clinique.",
            "llm_call_count": 0,
            "max_llm_calls": 1,
            "investigation_history": [],
            "technical_warnings": [],
        }
    )

    assert update["parsed_consultation"]
    assert update["technical_warnings"][0].startswith(
        "parse_consultation:ValueError:"
    )
