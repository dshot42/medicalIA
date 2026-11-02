from __future__ import annotations

from datetime import date

from app.agents.nodes.detect_findings import detect_findings
from app.agents.nodes.verify_sources import INSUFFICIENT_MESSAGE, verify_finding_sources
from app.models.document import DocumentChunk
from app.models.event import DatePrecision, EventStatus, EventType, MedicalEvent
from app.models.finding import Finding, FindingCategory, SourceReference, UncertaintyLevel


def event(identifier: str, day: date, kind: EventType, text: str) -> MedicalEvent:
    return MedicalEvent(
        id=identifier,
        event_date=day,
        date_precision=DatePrecision.EXACT,
        event_type=kind,
        description=text,
        source_document_id=f"doc_{identifier}",
        source_chunk_id=f"chunk_{identifier}",
        source_quote=text,
        confidence=0.9,
        status=EventStatus.VERIFIED,
    )


def test_detects_four_required_categories_without_claiming_causality() -> None:
    events = [
        event("none", date(2023, 9, 10), EventType.PRESCRIPTION, "Ne prend aucun traitement."),
        event("active", date(2023, 10, 1), EventType.PRESCRIPTION, "Traitement en cours."),
        event("rx", date(2024, 3, 12), EventType.PRESCRIPTION, "Prescription de Somnirel."),
        event("sym", date(2024, 4, 17), EventType.SYMPTOM, "Fatigue persistante."),
        event(
            "follow",
            date(2025, 3, 4),
            EventType.TEST_RECOMMENDED,
            "Contrôle biologique recommandé dans trois mois.",
        ),
        event(
            "missing",
            date(2021, 6, 8),
            EventType.OTHER,
            "Une IRM ancienne réalisée auparavant est mentionnée.",
        ),
    ]
    findings = detect_findings(events, {f"doc_{item.id}": f"{item.id}.txt" for item in events})
    categories = {finding.category for finding in findings}
    assert categories == set(FindingCategory)
    proximity = next(
        finding for finding in findings if finding.category == FindingCategory.TEMPORAL_PROXIMITY
    )
    assert "ne démontre pas de relation causale" in proximity.description
    assert all(finding.sources for finding in findings)


def test_insufficient_source_is_never_confirmed() -> None:
    finding = Finding(
        id="finding_bad",
        category=FindingCategory.TEMPORAL_PROXIMITY,
        description="Rapprochement à vérifier.",
        event_ids=["evt_1"],
        sources=[
            SourceReference(
                document_id="doc_1",
                chunk_id="chunk_1",
                filename="one.txt",
                quote="Citation absente",
            )
        ],
        uncertainty=UncertaintyLevel.MODERATE,
        confidence=0.8,
    )
    chunk = DocumentChunk(
        document_id="doc_1",
        filename="one.txt",
        page=1,
        chunk_id="chunk_1",
        text="Un autre contenu.",
        position=0,
    )
    verified = verify_finding_sources([finding], [chunk])[0]
    assert verified.sources_verified is False
    assert verified.verification_message == INSUFFICIENT_MESSAGE
    assert verified.uncertainty == UncertaintyLevel.HIGH
    assert verified.confidence <= 0.35

