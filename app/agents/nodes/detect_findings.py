from __future__ import annotations

import hashlib
import unicodedata

from app.agents.state import AnalysisState
from app.models.event import EventType, MedicalEvent
from app.models.finding import (
    Finding,
    FindingCategory,
    SourceReference,
    UncertaintyLevel,
)


def _normalize(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(char)
    )


def _source(
    event: MedicalEvent,
    filenames: dict[str, str],
    pages: dict[str, int | None] | None = None,
) -> SourceReference:
    return SourceReference(
        document_id=event.source_document_id,
        chunk_id=event.source_chunk_id,
        filename=filenames.get(event.source_document_id, "document inconnu"),
        page=(pages or {}).get(event.source_chunk_id),
        quote=event.source_quote,
    )


def _finding_id(category: FindingCategory, event_ids: list[str]) -> str:
    raw = f"{category.value}:{':'.join(sorted(event_ids))}"
    return f"finding_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def detect_findings(
    events: list[MedicalEvent],
    filenames: dict[str, str],
    pages: dict[str, int | None] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    dated = [event for event in events if event.event_date]

    no_treatment = [
        event
        for event in dated
        if "aucun traitement" in _normalize(event.description)
        or "ne prend pas de traitement" in _normalize(event.description)
    ]
    active_treatment = [
        event
        for event in dated
        if event.event_type == EventType.PRESCRIPTION
        and any(
            phrase in _normalize(event.description)
            for phrase in ("traitement en cours", "traitement par", "prescrit", "prescription")
        )
        and event not in no_treatment
    ]
    for first in no_treatment:
        for second in active_treatment:
            if first.source_document_id == second.source_document_id:
                continue
            if abs((first.event_date - second.event_date).days) <= 120:
                ids = [first.id, second.id]
                findings.append(
                    Finding(
                        id=_finding_id(FindingCategory.CONTRADICTION, ids),
                        category=FindingCategory.CONTRADICTION,
                        description=(
                            "Deux documents proches dans le temps décrivent différemment "
                            "la présence d'un traitement. Il s'agit d'une contradiction "
                            "documentaire potentielle à vérifier."
                        ),
                        event_ids=ids,
                        sources=[
                            _source(first, filenames, pages),
                            _source(second, filenames, pages),
                        ],
                        uncertainty=UncertaintyLevel.MODERATE,
                        confidence=0.88,
                        question_for_professional=(
                            "Quel traitement était effectivement pris pendant cette période ?"
                        ),
                        patient_id=first.patient_id,
                    )
                )

    prescriptions = [event for event in dated if event.event_type == EventType.PRESCRIPTION]
    symptoms = [event for event in dated if event.event_type == EventType.SYMPTOM]
    for prescription in prescriptions:
        for symptom in symptoms:
            delay = (symptom.event_date - prescription.event_date).days
            if 0 < delay <= 90:
                ids = [prescription.id, symptom.id]
                findings.append(
                    Finding(
                        id=_finding_id(FindingCategory.TEMPORAL_PROXIMITY, ids),
                        category=FindingCategory.TEMPORAL_PROXIMITY,
                        description=(
                            "Un symptôme a été rapporté après l'introduction documentée "
                            "d'un traitement. "
                            "Cette proximité chronologique ne démontre pas de relation causale."
                        ),
                        event_ids=ids,
                        sources=[
                            _source(prescription, filenames, pages),
                            _source(symptom, filenames, pages),
                        ],
                        uncertainty=UncertaintyLevel.HIGH,
                        confidence=0.75,
                        question_for_professional=(
                            "Cette succession temporelle mérite-t-elle d'être discutée "
                            "au regard du contexte clinique ?"
                        ),
                        patient_id=prescription.patient_id,
                    )
                )

    recommendations = [event for event in dated if event.event_type == EventType.TEST_RECOMMENDED]
    results = [
        event
        for event in dated
        if event.event_type in {EventType.TEST_RESULT, EventType.FOLLOW_UP}
    ]
    for recommendation in recommendations:
        later = [
            result
            for result in results
            if 0 < (result.event_date - recommendation.event_date).days <= 240
        ]
        if not later:
            ids = [recommendation.id]
            findings.append(
                Finding(
                    id=_finding_id(FindingCategory.MISSING_FOLLOW_UP, ids),
                    category=FindingCategory.MISSING_FOLLOW_UP,
                    description=(
                        "Aucun compte rendu de suivi correspondant n'a été identifié "
                        "dans les documents "
                        "importés. Cela ne signifie pas que le suivi n'a pas été réalisé."
                    ),
                    event_ids=ids,
                    sources=[_source(recommendation, filenames, pages)],
                    uncertainty=UncertaintyLevel.HIGH,
                    confidence=0.72,
                    question_for_professional=(
                        "Le suivi recommandé a-t-il été réalisé, et son compte rendu "
                        "est-il disponible ?"
                    ),
                    patient_id=recommendation.patient_id,
                )
            )

    exam_words = ("irm", "scanner", "radiographie", "echographie")
    reference_words = ("anterieur", "ancienne", "precedent", "realisee auparavant")
    references = [
        event
        for event in events
        if any(word in _normalize(event.description) for word in exam_words)
        and any(word in _normalize(event.description) for word in reference_words)
    ]
    for reference in references:
        before = [
            event
            for event in events
            if event.id != reference.id
            and event.event_type == EventType.TEST_RESULT
            and any(word in _normalize(event.description) for word in exam_words)
            and (
                reference.event_date is None
                or event.event_date is None
                or event.event_date < reference.event_date
            )
        ]
        if not before:
            ids = [reference.id]
            findings.append(
                Finding(
                    id=_finding_id(FindingCategory.MISSING_INFORMATION, ids),
                    category=FindingCategory.MISSING_INFORMATION,
                    description=(
                        "Un document mentionne un examen antérieur, mais aucun compte "
                        "rendu correspondant "
                        "n'a été retrouvé parmi les documents importés."
                    ),
                    event_ids=ids,
                    sources=[_source(reference, filenames, pages)],
                    uncertainty=UncertaintyLevel.HIGH,
                    confidence=0.78,
                    question_for_professional=(
                        "Le compte rendu de l'examen antérieur peut-il être ajouté au dossier ?"
                    ),
                    patient_id=reference.patient_id,
                )
            )
    return list({finding.id: finding for finding in findings}.values())


def detect_findings_node(state: AnalysisState) -> dict[str, object]:
    events = [MedicalEvent.model_validate(item) for item in state.get("timeline", [])]
    filenames = {
        str(chunk["document_id"]): str(chunk["filename"])
        for chunk in state.get("chunks", [])
    }
    pages = {
        str(chunk["chunk_id"]): int(chunk["page"]) if chunk.get("page") else None
        for chunk in state.get("chunks", [])
    }
    findings = detect_findings(events, filenames, pages)
    return {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "completed_nodes": [*state.get("completed_nodes", []), "DETECT_FINDINGS"],
    }

