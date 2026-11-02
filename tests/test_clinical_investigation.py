from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from app.agents.local_gguf import (
    GroundedHypothesisChoice,
    LLMOnlyHypothesisCandidate,
    LocalConsultationExtraction,
    LocalGGUFClient,
    LocalSymptomCandidate,
    QwenComparativeRanking,
    QwenGroundedHypothesisCandidate,
    QwenHypothesisReview,
)
from app.investigation.evidence import verify_patient_evidence
from app.investigation.parsing import parse_consultation_text
from app.models.investigation import (
    ConfidenceLevel,
    DiagnosticHypothesis,
    EvidencePolarity,
    EvidenceRelation,
    EvidenceRelationAssessment,
    EvidenceType,
    InvestigationStatus,
    PatientEvidence,
    SymptomStatus,
    VerificationStatus,
)

CONSULTATION = (
    "Patiente présentant depuis plusieurs mois des épisodes de douleurs abdominales intenses, "
    "accompagnés de nausées, de fatigue importante et parfois de sensations de faiblesse "
    "musculaire. Les épisodes surviennent de manière intermittente, "
    "sans cause clairement identifiée."
)


def load_patient(container, patient_id: str, directory: Path) -> None:
    for path in sorted(directory.glob("*")):
        if path.suffix in {".txt", ".md"}:
            container.document_service.import_document(path.name, path.read_bytes(), patient_id)


class FakeGroundedLocalLLM:
    enabled = True
    model_identifier = "fake-local.gguf"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def parse_consultation(self, text, deterministic_fallback):
        self.calls.append("parse_consultation")
        return deterministic_fallback

    def prioritize_hypotheses(self, parsed, facts, profiles):
        self.calls.append("prioritize_hypotheses")
        evidence_ids = [facts[0].evidence_id] if facts else []
        return [
            GroundedHypothesisChoice(
                disease_name=profiles[-1].disease_name,
                relevant_patient_evidence_ids=evidence_ids,
            )
        ]

    def generate_llm_only_differential(self, consultation_text):
        self.calls.append("generate_llm_only_differential")
        return [
            LLMOnlyHypothesisCandidate(
                disease_name=name,
                supporting_symptom_quotes=[quote],
                missing_information=["Examen clinique non fourni"],
                reasoning_summary=(
                    "Association possible à discuter, sans confirmation avec ces seules données."
                ),
            )
            for name, quote in (
                ("Hypothèse alpha", "douleurs abdominales intenses"),
                ("Hypothèse bêta", "nausées"),
                ("Hypothèse gamma", "fatigue importante"),
            )
        ]

    def generate_grounded_differential(
        self, consultation_text, facts, document_inventory, profiles=None, existing_hypotheses=None
    ):
        self.calls.append("generate_grounded_differential")
        self.received_profiles = profiles or []
        self.received_initial_fact_ids = {item.evidence_id for item in facts}
        return [
            QwenGroundedHypothesisCandidate(
                disease_name=f"Hypothèse Qwen {index}",
                reasoning_summary="Hypothèse de travail issue du dossier patient complet.",
                targeted_search_terms=[f"recherche patient {index}"],
            )
            for index in range(1, 6)
        ]

    def review_grounded_hypothesis(self, hypothesis, facts, profiles=None):
        self.calls.append(f"review:{hypothesis.disease_name}")
        self.last_review_fact_ids = {item.evidence_id for item in facts}
        self.last_review_profiles = profiles or []
        evidence_ids = [facts[0].evidence_id] if facts else []
        return QwenHypothesisReview(
            disease_name=hypothesis.disease_name,
            evidence_strength=ConfidenceLevel.PLAUSIBLE,
            supporting_evidence_ids=evidence_ids,
            contradicting_evidence_ids=[],
            discriminative_evidence_ids=evidence_ids,
            missing_information=["examen de confirmation non disponible"],
            chronology_assessed=True,
            reasoning_summary=(
                "Revue contradictoire Qwen fondée uniquement sur les preuves patient citées."
            ),
        )

    def compare_grounded_hypotheses(self, reviews):
        self.calls.append("compare_grounded_hypotheses")
        return QwenComparativeRanking(
            ordered_disease_names=[item.disease_name for item in reviews],
            can_prioritize_one=False,
            conclusion="Les éléments patient ne permettent pas de départager les hypothèses.",
        )


def test_comparison_failure_is_reported_and_never_keeps_initial_ranks(container) -> None:
    class FailingComparator:
        enabled = True
        model_identifier = "failing-test-model"

        def compare_grounded_hypotheses(self, reviews):
            raise ValueError("invalid structured output")

    graph = container.clinical_graph
    graph.local_llm = FailingComparator()
    hypotheses = [
        DiagnosticHypothesis(
            hypothesis_id=f"hyp-{index}",
            disease_name=f"Hypothèse {index}",
            investigation_status=InvestigationStatus.INVESTIGATED,
            reasoning_summary="Piste explorée sans preuve suffisante pour conclure.",
        ).model_dump(mode="json")
        for index in range(1, 4)
    ]
    result = graph.rank_qwen_differential(
        {
            "investigation_id": "comparison-failure-test",
            "patient_id": "patient-a",
            "started_at": datetime.now(UTC).isoformat(),
            "hypotheses": hypotheses,
            "extracted_clinical_facts": [],
            "coverage_report": {},
            "llm_call_count": 0,
            "max_llm_calls": 2,
            "iteration_count": 3,
            "investigation_history": [],
        }
    )

    assessment = result["final_assessment"]
    assert assessment["comparison_status"] == "failed"
    assert assessment["comparison_error"].startswith("ValueError:")
    assert assessment["technical_warnings"]
    assert [item["rank"] for item in assessment["differential_diagnoses"]] == [0, 0, 0]
    assert assessment["conclusion"].startswith("Échec technique explicite")


def test_uncalibrated_composite_score_is_not_presented_as_certainty(container) -> None:
    graph = container.clinical_graph
    graph.local_llm = FakeGroundedLocalLLM()
    hypotheses = []
    for index in range(1, 4):
        hypothesis = DiagnosticHypothesis(
            hypothesis_id=f"rate-{index}",
            disease_name=f"Hypothèse {index}",
            investigation_status=InvestigationStatus.INVESTIGATED,
            reasoning_summary="Hypothèse de test dont les preuves sont explicitement comptées.",
            evidence_relations=(
                [
                    EvidenceRelationAssessment(
                        evidence_id="evidence-support",
                        relation=EvidenceRelation.SUPPORTS,
                        relevant=True,
                    ),
                    EvidenceRelationAssessment(
                        evidence_id="evidence-contradiction",
                        relation=EvidenceRelation.CONTRADICTS,
                        relevant=True,
                    ),
                ]
                if index == 1
                else []
            ),
        )
        hypotheses.append(hypothesis.model_dump(mode="json"))
    result = graph.rank_qwen_differential({
        "investigation_id": "certainty-rate-test",
        "patient_id": "patient-a",
        "started_at": datetime.now(UTC).isoformat(),
        "hypotheses": hypotheses,
        "extracted_clinical_facts": [],
        "coverage_report": {
            "passages_examined": 4,
            "passages_total": 8,
            "complete": False,
        },
        "llm_call_count": 0,
        "max_llm_calls": 1,
        "iteration_count": 3,
        "investigation_history": [],
    })
    ranked = result["final_assessment"]["differential_diagnoses"]

    assert ranked[0]["certainty_index"] == 0
    assert "non calculé" in ranked[0]["certainty_index_basis"]
    dimensions = ranked[0]["documentary_assessment"]
    assert dimensions["validated_support_count"] == 0
    assert dimensions["validated_contradiction_count"] == 0
    assert dimensions["passages_examined"] == 4
    assert dimensions["passages_total"] == 8


def test_qwen_retries_incomplete_patient_archive_inventory_with_a_hard_limit(container) -> None:
    graph = container.clinical_graph
    state = {
        "investigation_mode": "qwen_grounded",
        "coverage_report": {"complete": False},
        "inventory_attempt_count": 1,
    }
    assert graph._route_after_document_coverage(state) == "retry_inventory"
    state["inventory_attempt_count"] = 3
    assert graph._route_after_document_coverage(state) == "rank_partial"
    state["coverage_report"] = {"complete": True}
    assert graph._route_after_document_coverage(state) == "qwen"


def test_parse_consultation_distinguishes_present_denied_and_unknown() -> None:
    parsed = parse_consultation_text(
        "Douleurs abdominales intermittentes avec fatigue, sans vomissements depuis trois mois."
    )
    statuses = {item.name: item.status for item in parsed.symptoms}
    assert statuses["douleurs abdominales"] == SymptomStatus.PRESENT
    assert statuses["fatigue"] == SymptomStatus.PRESENT
    assert statuses["vomissements"] == SymptomStatus.DENIED
    assert "relation avec les repas et le transit" in parsed.not_documented
    assert "nausées" not in statuses  # non documenté n'est jamais transformé en négatif


def test_local_llm_is_used_by_two_graph_nodes_with_a_bounded_budget(
    container, sample_dir
) -> None:
    patient_dir = sample_dir.parent / "patients" / "client_test-1"
    load_patient(container, "client_test-1", patient_dir)
    fake = FakeGroundedLocalLLM()
    container.clinical_graph.local_llm = fake

    result = container.investigation_service.run(
        "client_test-1", CONSULTATION, max_iterations=1
    )

    assert fake.calls == ["parse_consultation", "prioritize_hypotheses"]
    assert result["llm_call_count"] == 2
    details = " ".join(item["details"] for item in result["investigation_history"])
    assert "fake-local.gguf" in details


def test_local_llm_rejects_a_symptom_without_an_exact_supporting_quote(container) -> None:
    client = LocalGGUFClient(container.settings)
    fallback = parse_consultation_text("Fatigue persistante, sans fièvre.")

    def fake_generation(*args, **kwargs):
        return LocalConsultationExtraction(
            symptoms=[
                LocalSymptomCandidate(
                    name="fatigue",
                    status=SymptomStatus.PRESENT,
                    source_quote="Fatigue persistante",
                ),
                LocalSymptomCandidate(
                    name="paralysie",
                    status=SymptomStatus.PRESENT,
                    source_quote="paralysie complète",
                ),
            ]
        )

    client.generate_structured = fake_generation  # type: ignore[method-assign]
    parsed = client.parse_consultation("Fatigue persistante, sans fièvre.", fallback)
    assert "fatigue" in {item.name for item in parsed.symptoms}
    assert "paralysie" not in {item.name for item in parsed.symptoms}


def test_local_llm_consultation_uses_compact_contract_and_sufficient_budget(container) -> None:
    client = LocalGGUFClient(container.settings)
    fallback = parse_consultation_text("Fatigue persistante, sans fièvre.")
    captured: dict[str, object] = {}

    def fake_generation(*args, **kwargs):
        captured.update(kwargs)
        return LocalConsultationExtraction()

    client.generate_structured = fake_generation  # type: ignore[method-assign]
    client.parse_consultation("Fatigue persistante, sans fièvre.", fallback)

    contract = str(captured["json_contract"])
    assert '"symptoms"' in contract
    assert "$defs" not in contract
    assert captured["max_tokens"] >= 800


def test_laura_differential_has_independent_reference_profiles(container) -> None:
    generated_hypotheses = [
        "Encéphalite à virus Nipah",
        "Encéphalite herpétique",
        "Encéphalite japonaise",
        "Encéphalite auto-immune aiguë",
        "Paludisme grave",
        "Meningite bactérienne",
    ]

    assert all(
        container.knowledge_service.match_profile(name) is not None
        for name in generated_hypotheses
    )


def test_llm_only_mode_uses_no_patient_record_or_medical_knowledge(
    container, sample_dir, monkeypatch
) -> None:
    patient_dir = sample_dir.parent / "patients" / "client_test-1"
    load_patient(container, "client_test-1", patient_dir)
    fake = FakeGroundedLocalLLM()
    container.clinical_graph.local_llm = fake
    container.clinical_graph.investigation_mode = "llm_only"

    def forbidden(*args, **kwargs):
        raise AssertionError("Le mode LLM seul ne doit consulter aucune autre source.")

    monkeypatch.setattr(container.patient_history_retriever, "initial_search", forbidden)
    monkeypatch.setattr(container.knowledge_service, "search", forbidden)
    result = container.investigation_service.run("client_test-1", CONSULTATION)

    assert fake.calls == ["parse_consultation", "generate_llm_only_differential"]
    assert result["investigation_mode"] == "llm_only"
    assert result["clinical_evidence"] == []
    assert result["medical_knowledge_evidence"] == []
    diagnoses = result["final_assessment"]["differential_diagnoses"]
    assert len(diagnoses) == 3
    assert all(item["evidence_strength"].startswith("non évaluable") for item in diagnoses)
    assert all(item["symptom_quotes"] for item in diagnoses)


def test_qwen_grounded_mode_uses_patient_record_and_independent_knowledge(
    container, sample_dir
) -> None:
    patient_dir = sample_dir.parent / "patients" / "client_test-1"
    load_patient(container, "client_test-1", patient_dir)
    fake = FakeGroundedLocalLLM()
    container.clinical_graph.local_llm = fake
    container.clinical_graph.investigation_mode = "qwen_grounded"

    result = container.investigation_service.run("client_test-1", CONSULTATION)

    assert result["investigation_mode"] == "qwen_grounded"
    assert result["coverage_report"]["documents_processed"] == 8
    assert result["coverage_report"]["complete"] is True
    assert fake.received_profiles
    assert result["medical_knowledge_evidence"] == []  # fake candidates do not match profile names
    assert result["iteration_count"] == 5
    assert result["llm_call_count"] == 3
    assert fake.calls[0:2] == ["parse_consultation", "generate_grounded_differential"]
    assert fake.calls == [
        "parse_consultation",
        "generate_grounded_differential",
        "compare_grounded_hypotheses",
    ]
    diagnoses = result["final_assessment"]["differential_diagnoses"]
    assert len(diagnoses) == 5
    assert all(item["medical_knowledge_evidence"] == [] for item in diagnoses)
    assert all(
        all(reference.startswith("patient:") for reference in item["source_references"])
        for item in diagnoses
    )
    assert result["final_assessment"]["conclusion"].startswith(
        "La comparaison technique est achevée"
    )


def test_new_patient_retrieval_is_merged_and_passed_to_following_review(
    container, sample_dir, monkeypatch
) -> None:
    load_patient(container, "patient-a", sample_dir)
    load_patient(
        container,
        "patient-b",
        sample_dir.parent / "patients" / "client_control-2",
    )
    patient_chunks = container.repository.list_chunks(patient_id="patient-a")
    foreign_chunk = container.repository.list_chunks(patient_id="patient-b")[0]
    candidate = patient_chunks[0]
    fake = FakeGroundedLocalLLM()
    graph = container.clinical_graph
    graph.local_llm = fake
    monkeypatch.setattr(
        container.patient_history_retriever,
        "semantic_patient_search",
        lambda patient_id, queries: ([candidate, foreign_chunk], ["recherche ciblee"]),
    )
    state = {
        "patient_id": "patient-a",
        "selected_hypothesis_id": "hyp-test",
        "hypotheses": [{
            "hypothesis_id": "hyp-test",
            "disease_name": "Hypothèse test",
            "reasoning_summary": "Piste clinique de test à évaluer avec les données du dossier.",
            "targeted_search_terms": ["symptôme ciblé"],
        }],
        "retrieved_evidence": [],
        "retrieved_chunks": [],
        "raw_clinical_evidence": [],
        "extracted_clinical_facts": [],
        "evidence_history": [],
        "evidence_revision": 0,
        "targeted_queries": [],
        "investigation_history": [],
        "llm_call_count": 0,
        "max_llm_calls": 4,
        "iteration_count": 1,
        "investigated_hypotheses": [],
    }
    update = graph.retrieve_qwen_targeted_evidence(state)
    merged = {**state, **update}

    assert update["last_new_evidence_count"] > 0
    assert candidate.chunk_id in {item["chunk_id"] for item in update["retrieved_chunks"]}
    assert all(item["patient_id"] == "patient-a" for item in update["retrieved_chunks"])
    assert update["evidence_revision"] > state["evidence_revision"]
    review = graph.review_qwen_hypothesis(merged)
    assert review["llm_call_count"] == 0
    assert "review_grounded_hypothesis" not in fake.calls
    assert {
        item["evidence_id"] for item in update["new_evidence"]
    } <= {
        item["evidence_id"] for item in merged["extracted_clinical_facts"]
    }


def test_new_discriminating_evidence_changes_hypothesis_assessment(container) -> None:
    graph = container.clinical_graph
    fake = FakeGroundedLocalLLM()
    graph.local_llm = fake
    base = {
        "patient_id": "patient-a",
        "selected_hypothesis_id": "hyp-aip",
        "hypotheses": [{
            "hypothesis_id": "hyp-aip",
            "disease_name": "Porphyrie aiguë intermittente",
            "reasoning_summary": "Hypothèse étiologique à évaluer au regard du dossier.",
            "targeted_search_terms": ["crise"],
        }],
        "extracted_clinical_facts": [],
        "investigated_hypotheses": [],
        "investigation_history": [],
        "llm_call_count": 0,
        "max_llm_calls": 5,
        "evidence_revision": 0,
    }
    first = graph.review_qwen_hypothesis(base)
    first_hypothesis = first["hypotheses"][0]
    assert first_hypothesis["confidence_level"] == ConfidenceLevel.NOT_ASSESSABLE.value

    fact = PatientEvidence(
        evidence_id="pev_new_sodium_pattern",
        raw_evidence_id="raw_new_sodium_pattern",
        patient_id="patient-a",
        type=EvidenceType.LABORATORY,
        description="Hyponatrémie pendant une crise abdominale.",
        quote="Hyponatrémie pendant une crise abdominale.",
        document_id="doc-a",
        chunk_id="chunk-a",
        filename="note.txt",
        polarity=EvidencePolarity.SUPPORTING,
        verification_status=VerificationStatus.VERIFIED,
    )
    second_state = {
        **base,
        **first,
        "hypotheses": [first_hypothesis],
        "extracted_clinical_facts": [fact.model_dump(mode="json")],
        "evidence_revision": 1,
        "llm_call_count": first["llm_call_count"],
    }
    second = graph.review_qwen_hypothesis(second_state)

    assert second["hypotheses"][0]["confidence_level"] == (
        ConfidenceLevel.WEAKLY_SUPPORTED.value
    )
    assert second["hypotheses"][0]["diagnostic_status"] == "POSSIBLE"
    assert second["hypotheses"][0]["evidence_revision"] == 1


def test_iterative_investigation_is_sourced_contradictory_and_patient_scoped(
    container, sample_dir
) -> None:
    patient_root = sample_dir.parent / "patients"
    load_patient(container, "client_test-1", patient_root / "client_test-1")
    load_patient(container, "client_control-2", patient_root / "client_control-2")

    result = container.investigation_service.run("client_test-1", CONSULTATION)
    assessment = result["final_assessment"]
    diagnoses = assessment["differential_diagnoses"]

    assert 3 <= len(result["hypotheses"]) <= 8
    assert result["iteration_count"] >= 3
    assert len(result["targeted_queries"]) > len(result["hypotheses"])
    assert all(item["patient_id"] == "client_test-1" for item in result["clinical_evidence"])
    assert not any(
        item["filename"] in {"01_consultation.txt", "02_imagerie.txt"}
        for item in result["clinical_evidence"]
    )
    assert all(
        item["verification_status"] == VerificationStatus.VERIFIED
        for item in result["clinical_evidence"]
    )
    assert all(item["evidence_id"].startswith("pev_") for item in result["clinical_evidence"])
    sodium = next(
        item
        for item in result["clinical_evidence"]
        if "sodium" in item["description"].lower()
    )
    assert sodium["numeric_observations"][0]["value"] == 126.0
    assert sodium["numeric_observations"][0]["unit"] == "mmol/l"
    source_chunk = container.repository.get_chunk(sodium["chunk_id"], "client_test-1")
    assert sodium["quote"] in source_chunk.text
    assert all(
        item["knowledge_id"].startswith("kb_")
        and item["source_url"].startswith("https://")
        for item in result["medical_knowledge_evidence"]
    )
    assert container.knowledge_vector_store.count() == len(
        container.knowledge_service.profiles
    )
    cross_patient_search = container.retriever.search(
        "fracture genou", patient_id="client_test-1"
    )
    assert all(item["patient_id"] == "client_test-1" for item in cross_patient_search)
    assert assessment["requires_clinician_review"] is True
    assert all("%" not in item["clinical_reasoning"] for item in diagnoses)

    gallstones = next(
        item
        for item in diagnoses
        if item["disease_name"] == "Lithiase biliaire symptomatique"
    )
    assert gallstones["contradicting_evidence"]
    assert gallstones["rank"] > 1

    nodes = [item["node"] for item in result["investigation_history"]]
    assert nodes.count("select_hypothesis") == result["iteration_count"]
    assert nodes.count("retrieve_targeted_evidence") == result["iteration_count"]
    assert nodes.count("challenge_hypothesis") == result["iteration_count"]
    assert nodes[-1] == "rank_differential_diagnoses"

    stored = container.investigation_service.get(
        result["investigation_id"], "client_test-1"
    )
    assert stored is not None
    assert stored["final_assessment"] == assessment


def test_iteration_budget_insufficient_evidence_and_rerun_history(container, sample_dir) -> None:
    patient_dir = sample_dir.parent / "patients" / "client_test-1"
    load_patient(container, "client_test-1", patient_dir)
    limited = container.investigation_service.run(
        "client_test-1", CONSULTATION, max_iterations=2
    )
    assert limited["iteration_count"] == 2
    assert limited["stop_reason"] == "Budget maximal d'itérations atteint."

    insufficient = container.investigation_service.run(
        "client_test-1", "Sensation générale atypique sans autre élément descriptif disponible."
    )
    assert insufficient["hypotheses"] == []
    assert (
        insufficient["final_assessment"]["conclusion"]
        == "Aucune hypothèse ne peut être privilégiée avec suffisamment d'éléments."
    )

    rerun = container.investigation_service.rerun(
        limited["investigation_id"], "client_test-1"
    )
    assert rerun["investigation_id"] != limited["investigation_id"]
    history = container.investigation_service.list("client_test-1")
    original = next(
        item
        for item in history
        if item["investigation_id"] == limited["investigation_id"]
    )
    child = next(item for item in history if item["investigation_id"] == rerun["investigation_id"])
    assert original["final_assessment"] == limited["final_assessment"]
    assert child["parent_investigation_id"] == limited["investigation_id"]


def test_redundant_queries_stop_and_invalid_patient_citation_is_rejected(
    container, sample_dir
) -> None:
    patient_dir = sample_dir.parent / "patients" / "client_test-1"
    load_patient(container, "client_test-1", patient_dir)
    result = container.investigation_service.run(
        "client_test-1", CONSULTATION, max_iterations=2
    )
    state = deepcopy(
        container.investigation_service.get(result["investigation_id"], "client_test-1")[
            "state"
        ]
    )
    remaining = next(
        item
        for item in state["hypotheses"]
        if item["hypothesis_id"] not in state["investigated_hypotheses"]
    )
    state["targeted_queries"].append(f"{remaining['hypothesis_id']}|requête déjà exécutée")
    for hypothesis in state["hypotheses"]:
        if hypothesis["hypothesis_id"] not in state["investigated_hypotheses"]:
            state["targeted_queries"].append(
                f"{hypothesis['hypothesis_id']}|requête déjà exécutée"
            )
    state["iteration_count"] = 1
    decision = container.clinical_graph.should_continue_investigation(state)
    assert decision["stop_reason"] == "Les recherches restantes seraient redondantes."

    source = container.repository.list_chunks(patient_id="client_test-1")[0]
    invalid = PatientEvidence(
        evidence_id="pev_invalid",
        patient_id="client_control-2",
        type=EvidenceType.SYMPTOM,
        description="Citation inventée",
        quote="Citation inventée",
        document_id=source.document_id,
        chunk_id=source.chunk_id,
        filename=source.filename,
    )
    assert verify_patient_evidence([invalid], container.repository.get_chunk) == []


def test_initially_obvious_hypothesis_is_downgraded_when_contradicted(container) -> None:
    patient_id = "counterexample"
    container.document_service.import_document(
        "symptoms.txt",
        (
            "Date du document : 2 mai 2025. Douleur soudaine de l'abdomen supérieur droit "
            "après les repas avec nausées."
        ).encode(),
        patient_id,
    )
    container.document_service.import_document(
        "imaging.txt",
        (
            "Date du document : 3 mai 2025. L'échographie abdominale ne montre aucun "
            "calcul biliaire et les voies biliaires sont normales."
        ).encode(),
        patient_id,
    )
    result = container.investigation_service.run(
        patient_id,
        "Douleur de l'abdomen supérieur droit après les repas avec nausées intermittentes.",
    )
    gallstones = next(
        item
        for item in result["final_assessment"]["differential_diagnoses"]
        if item["disease_name"] == "Lithiase biliaire symptomatique"
    )
    assert gallstones["contradicting_evidence"]
    assert gallstones["evidence_strength"] != "fortement étayée"

