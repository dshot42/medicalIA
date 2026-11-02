from __future__ import annotations

from app.agents.local_gguf import LocalGGUFClient, QwenGroundedDifferentialOutput
from app.investigation.evidence import (
    extract_evidence_from_chunks,
    extract_raw_evidence_from_chunks,
)
from app.investigation.validation import validate_evidence_relations
from app.models.document import DocumentChunk
from app.models.investigation import (
    EvidencePolarity,
    EvidenceRelation,
    EvidenceType,
    MedicalKnowledgeProfile,
    VerificationStatus,
)


def _chunk(text: str, patient_id: str = "patient-a") -> DocumentChunk:
    return DocumentChunk(
        document_id="doc-a",
        filename="note.txt",
        page=1,
        chunk_id="chunk-a",
        text=text,
        position=0,
        patient_id=patient_id,
    )


def test_unclassified_source_information_is_never_discarded() -> None:
    text = "Une observation clinique inhabituelle reste sans cat\u00e9gorie pr\u00e9d\u00e9finie."
    chunk = _chunk(text)

    raw = extract_raw_evidence_from_chunks([chunk])
    structured = extract_evidence_from_chunks([chunk])

    assert [item.text for item in raw] == [text]
    assert [item.description for item in structured] == [text]
    assert structured[0].type == EvidenceType.OTHER
    assert structured[0].raw_evidence_id == raw[0].raw_evidence_id


def test_unperformed_specialized_tests_are_preserved_as_missing_not_negative() -> None:
    text = (
        "Le porphobilinog\u00e8ne urinaire et l'acide delta-aminol\u00e9vulinique urinaire "
        "n'ont pas \u00e9t\u00e9 recherch\u00e9s pendant les sympt\u00f4mes."
    )
    evidence = extract_evidence_from_chunks([_chunk(text)])

    assert len(evidence) == 1
    assert evidence[0].type == EvidenceType.UNPERFORMED_TEST
    assert evidence[0].polarity == EvidencePolarity.NEUTRAL


def test_episode_duration_is_structured_and_kept() -> None:
    text = (
        "Les \u00e9pisodes durent g\u00e9n\u00e9ralement deux \u00e0 quatre jours puis "
        "r\u00e9gressent."
    )
    evidence = extract_evidence_from_chunks([_chunk(text)])

    assert len(evidence) == 1
    assert evidence[0].type == EvidenceType.DURATION


def test_unperformed_profile_test_is_missing_and_irrelevant_negative_is_not_contradiction() -> None:
    profile = MedicalKnowledgeProfile(
        knowledge_id="profile-a",
        disease_name="Maladie endocrine fictive",
        aliases=[],
        typical_manifestations=["fatigue persistante"],
        discriminating_features=["dosage hormonal spécifique"],
        useful_tests=["dosage hormonal spécifique"],
        differential_diagnoses=[],
        contradiction_markers=["absence de faiblesse musculaire"],
        source_title="Référentiel fictif",
        source_url="https://example.invalid",
        source_organization="Test",
        last_reviewed="2026-01",
        curated_at="2026-01-01",
    )
    missing = extract_evidence_from_chunks(
        [_chunk("Le dosage hormonal spécifique n'a pas été réalisé.")]
    )[0]
    missing.verification_status = VerificationStatus.VERIFIED
    unrelated = extract_evidence_from_chunks(
        [_chunk("L'échographie abdominale est normale.")]
    )[0]
    unrelated.verification_status = VerificationStatus.VERIFIED
    relations = validate_evidence_relations(
        [missing, unrelated],
        profile,
        proposed_contradicting_ids=[unrelated.evidence_id],
    )
    by_id = {item.evidence_id: item.relation for item in relations}
    assert by_id[missing.evidence_id] == EvidenceRelation.MISSING
    assert by_id[unrelated.evidence_id] == EvidenceRelation.UNVERIFIABLE


def test_invalid_truncated_json_gets_one_bounded_structural_repair() -> None:
    raw = '''{"hypotheses":[
      {"disease_name":"Hypothèse alpha","reasoning_summary":
       "Piste de travail à évaluer.","targeted_search_terms":["alpha"]},
      {"disease_name":"Hypothèse bêta","reasoning_summary":
       "Piste de travail à évaluer.","targeted_search_terms":["beta"]},
      {"disease_name":"Hypothèse gamma","reasoning_summary":
       "Piste de travail à évaluer.","targeted_search_terms":["gamma"]}
    '''
    repaired = LocalGGUFClient._validate_json_content(raw, QwenGroundedDifferentialOutput)
    assert len(repaired.hypotheses) == 3
