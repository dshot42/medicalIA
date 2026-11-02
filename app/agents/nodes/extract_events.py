from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date

from app.agents.state import AnalysisState
from app.models.document import DocumentChunk
from app.models.event import DatePrecision, EventStatus, EventType, MedicalEvent

MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def _normalize(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(char)
    )


def parse_date(text: str) -> tuple[date | None, DatePrecision]:
    normalized = _normalize(text)
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2}|19\d{2})\b", normalized)
    if match:
        try:
            return date(int(match[3]), int(match[2]), int(match[1])), DatePrecision.EXACT
        except ValueError:
            return None, DatePrecision.UNKNOWN
    month_pattern = "|".join(MONTHS)
    match = re.search(rf"\b(\d{{1,2}})\s+({month_pattern})\s+(20\d{{2}}|19\d{{2}})\b", normalized)
    if match:
        try:
            return date(int(match[3]), MONTHS[match[2]], int(match[1])), DatePrecision.EXACT
        except ValueError:
            return None, DatePrecision.UNKNOWN
    match = re.search(rf"\b({month_pattern})\s+(20\d{{2}}|19\d{{2}})\b", normalized)
    if match:
        return date(int(match[2]), MONTHS[match[1]], 1), DatePrecision.MONTH
    match = re.search(r"\b(20\d{2}|19\d{2})\b", normalized)
    if match:
        return date(int(match[1]), 1, 1), DatePrecision.YEAR
    return None, DatePrecision.UNKNOWN


def classify_event(text: str) -> EventType | None:
    value = _normalize(text)
    if any(word in value for word in ("hospitalis", "admission aux urgences")):
        return EventType.HOSPITALIZATION
    if any(word in value for word in ("prescrit", "prescription", "medicament")) or (
        "traitement" in value and "relation avec" not in value
    ):
        return EventType.PRESCRIPTION
    if any(
        word in value
        for word in (
            "controle biologique",
            "examen recommande",
            "irm recommand",
            "scanner recommande",
            "analyse a realiser",
            "suivi dans",
        )
    ):
        return EventType.TEST_RECOMMENDED
    if any(word in value for word in ("resultat", "bilan", "analyse montre", "examen normal")):
        return EventType.TEST_RESULT
    if any(word in value for word in ("fatigue", "douleur", "nausee", "vertige", "symptome")):
        return EventType.SYMPTOM
    if any(word in value for word in ("consultation", "consulte", "visite")):
        return EventType.CONSULTATION
    if any(word in value for word in ("suivi", "controle")):
        return EventType.FOLLOW_UP
    if any(word in value for word in ("irm", "scanner", "radiographie", "echographie")):
        return EventType.OTHER
    return None


def extract_events_from_chunks(chunks: list[DocumentChunk]) -> list[MedicalEvent]:
    events: list[MedicalEvent] = []
    document_dates: dict[str, tuple[date | None, DatePrecision]] = {}
    for chunk in chunks:
        if chunk.document_id not in document_dates:
            document_dates[chunk.document_id] = parse_date(chunk.text[:250])
        statements = [item.strip(" \t-•") for item in re.split(r"(?<=[.!?])\s+|\n+", chunk.text)]
        for statement in statements:
            if len(statement) < 8:
                continue
            event_type = classify_event(statement)
            if event_type is None:
                continue
            event_date, precision = parse_date(statement)
            confidence = 0.94
            if event_date is None:
                event_date, precision = document_dates[chunk.document_id]
                confidence = 0.84 if event_date else 0.62
            identity = f"{chunk.chunk_id}:{statement}:{event_type.value}"
            event_id = f"evt_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
            events.append(
                MedicalEvent(
                    id=event_id,
                    event_date=event_date,
                    date_precision=precision,
                    event_type=event_type,
                    description=statement,
                    source_document_id=chunk.document_id,
                    source_chunk_id=chunk.chunk_id,
                    source_quote=statement,
                    confidence=confidence,
                    status=EventStatus.EXTRACTED if event_date else EventStatus.UNCERTAIN,
                    patient_id=chunk.patient_id,
                )
            )
    unique = {event.id: event for event in events}
    return list(unique.values())


def extract_events_node(state: AnalysisState) -> dict[str, object]:
    chunks = [DocumentChunk.model_validate(item) for item in state.get("chunks", [])]
    events = extract_events_from_chunks(chunks)
    return {
        "events": [event.model_dump(mode="json") for event in events],
        "completed_nodes": [*state.get("completed_nodes", []), "EXTRACT_EVENTS"],
    }

