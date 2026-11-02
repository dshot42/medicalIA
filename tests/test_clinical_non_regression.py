from __future__ import annotations


def _import(container, patient_id: str, filename: str, text: str) -> None:
    container.document_service.import_document(filename, text.encode("utf-8"), patient_id)


def _diagnosis(result, name: str) -> dict[str, object]:
    return next(
        item
        for item in result["final_assessment"]["differential_diagnoses"]
        if item["disease_name"] == name
    )


def test_common_disease_can_outrank_rare_and_adrenal_hypotheses(container) -> None:
    patient = "regression-common"
    _import(
        container,
        patient,
        "consultation.txt",
        "Date du document : 2 mai 2025. Douleur abdominale soudaine de l'abdomen "
        "supérieur droit après un repas copieux avec nausées.",
    )
    _import(
        container,
        patient,
        "echographie.txt",
        "Date du document : 3 mai 2025. L'échographie abdominale montre plusieurs "
        "calculs biliaires.",
    )
    result = container.investigation_service.run(
        patient, "Douleur abdominale supérieure droite après les repas avec nausées."
    )
    diagnoses = result["final_assessment"]["differential_diagnoses"]
    assert diagnoses[0]["disease_name"] == "Lithiase biliaire symptomatique"
    assert diagnoses[0]["source_references"]
    assert 0 < diagnoses[0]["reliability_rate"] <= 100
    assert diagnoses[0]["reliability_explanation"].startswith(
        "Alias historique de la couverture documentaire"
    )
    assert diagnoses[0]["documentary_assessment"]["documentary_coverage"] == (
        "complete"
    )


def test_rare_disease_is_kept_only_when_discriminating_pattern_supports_it(container) -> None:
    patient = "regression-rare"
    _import(
        container,
        patient,
        "crises.txt",
        "Date du document : 4 juin 2025. Crises intermittentes de douleur abdominale "
        "diffuse sévère avec nausées et faiblesse musculaire transitoire.",
    )
    _import(
        container,
        patient,
        "biologie.txt",
        "Date du document : 4 juin 2025. Pendant la crise, sodium 124 mmol/L, "
        "hyponatrémie. Potassium 4,2 mmol/L normal.",
    )
    _import(
        container,
        patient,
        "declencheurs.txt",
        "Date du document : 8 juin 2025. Deux crises ont débuté après un jeûne et "
        "une période de stress.",
    )
    result = container.investigation_service.run(
        patient,
        "Douleurs abdominales sévères intermittentes avec nausées et faiblesse "
        "musculaire après un jeûne.",
    )
    aip = _diagnosis(result, "Porphyrie aiguë intermittente")
    adrenal = _diagnosis(result, "Insuffisance surrénalienne primaire")
    assert aip["rank"] < adrenal["rank"]
    assert aip["discriminative_findings"]
    assert "porphobilinogène urinaire sur échantillon pendant les symptômes" in aip[
        "missing_information"
    ]


def test_explicit_contradiction_is_not_treated_as_missing_information(container) -> None:
    patient = "regression-contradiction"
    _import(
        container,
        patient,
        "symptomes.txt",
        "Date du document : 2 mai 2025. Douleur abdominale de l'hypochondre droit "
        "après les repas avec nausées.",
    )
    _import(
        container,
        patient,
        "imagerie.txt",
        "Date du document : 3 mai 2025. L'échographie abdominale ne montre aucun "
        "calcul biliaire.",
    )
    result = container.investigation_service.run(
        patient, "Douleur abdominale de l'hypochondre droit après les repas avec nausées."
    )
    gallstones = _diagnosis(result, "Lithiase biliaire symptomatique")
    assert gallstones["contradicting_evidence"]
    assert gallstones["evidence_strength"] != "fortement étayée"


def test_missing_specialized_laboratory_result_stays_missing(container) -> None:
    patient = "regression-missing-lab"
    _import(
        container,
        patient,
        "consultation.txt",
        "Date du document : 12 juillet 2025. Fatigue persistante, nausées, douleur "
        "abdominale et faiblesse musculaire. Le cortisol matinal n'a pas été mesuré.",
    )
    result = container.investigation_service.run(
        patient,
        "Fatigue persistante avec nausées, douleurs abdominales et faiblesse musculaire.",
    )
    adrenal = _diagnosis(result, "Insuffisance surrénalienne primaire")
    assert adrenal["evidence_strength"] != "fortement étayée"
    assert "cortisol matinal" in adrenal["missing_information"]
    assert not any(
        "cortisol" in item.lower()
        for item in adrenal["contradicting_evidence"]
    )


def test_no_hypothesis_can_be_preferred_is_an_explicit_result(container) -> None:
    patient = "regression-indeterminate"
    _import(
        container,
        patient,
        "note.txt",
        "Date du document : 1 août 2025. Sensation générale atypique. Aucun examen "
        "complémentaire n'est disponible.",
    )
    result = container.investigation_service.run(
        patient, "Sensation générale atypique sans autre élément descriptif disponible."
    )
    assert result["hypotheses"] == []
    assert result["final_assessment"]["conclusion"] == (
        "Aucune hypothèse ne peut être privilégiée avec suffisamment d'éléments."
    )


def test_every_inventory_document_is_processed_not_only_vector_hits(container) -> None:
    patient = "regression-coverage"
    for index in range(10):
        text = (
            f"Date du document : {index + 1} septembre 2025. "
            + (
                "Sodium 128 mmol/L, hyponatrémie pendant la douleur abdominale."
                if index == 9
                else f"Note de suivi numéro {index + 1} sans nouvelle conclusion médicale."
            )
        )
        _import(container, patient, f"{index + 1:02d}.txt", text)
    result = container.investigation_service.run(
        patient, "Douleurs abdominales intermittentes avec fatigue et nausées."
    )
    coverage = result["coverage_report"]
    assert coverage["documents_total"] == 10
    assert coverage["documents_processed"] == 10
    assert coverage["complete"] is True
    assert any(
        item["numeric_observations"] and item["numeric_observations"][0]["value"] == 128.0
        for item in result["clinical_evidence"]
    )
