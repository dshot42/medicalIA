from __future__ import annotations

from app.agents.nodes.build_timeline import build_timeline
from app.agents.nodes.extract_events import extract_events_from_chunks
from app.models.document import DocumentChunk
from app.models.event import EventStatus


def make_chunk(chunk_id: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        document_id=f"doc_{chunk_id}",
        filename=f"{chunk_id}.txt",
        page=1,
        chunk_id=chunk_id,
        text=text,
        position=0,
    )


def test_timeline_orders_dated_events_and_keeps_unknown_dates() -> None:
    chunks = [
        make_chunk("late", "Consultation le 12 mars 2024."),
        make_chunk("unknown", "Fatigue rapportée, sans date indiquée."),
        make_chunk("early", "Consultation le 2 janvier 2020."),
    ]
    timeline = build_timeline(extract_events_from_chunks(chunks))
    assert timeline[0].event_date.isoformat() == "2020-01-02"
    assert timeline[1].event_date.isoformat() == "2024-03-12"
    assert timeline[-1].event_date is None
    assert timeline[-1].status == EventStatus.UNCERTAIN

