from __future__ import annotations

from app.agents.state import AnalysisState
from app.models.document import DocumentChunk
from app.models.event import EventStatus, MedicalEvent


def validate_events_node(state: AnalysisState) -> dict[str, object]:
    chunks = {
        chunk.chunk_id: chunk
        for chunk in (DocumentChunk.model_validate(item) for item in state.get("chunks", []))
    }
    valid_events: list[MedicalEvent] = []
    errors = list(state.get("errors", []))
    for event_data in state.get("events", []):
        event = MedicalEvent.model_validate(event_data)
        chunk = chunks.get(event.source_chunk_id)
        source_valid = (
            chunk is not None
            and chunk.document_id == event.source_document_id
            and event.source_quote in chunk.text
        )
        if not source_valid:
            errors.append(f"Source invalide pour l'événement {event.id}")
            continue
        event.status = EventStatus.VERIFIED if event.event_date else EventStatus.UNCERTAIN
        valid_events.append(event)
    return {
        "events": [event.model_dump(mode="json") for event in valid_events],
        "errors": errors,
        "completed_nodes": [*state.get("completed_nodes", []), "VALIDATE_EVENTS"],
    }

