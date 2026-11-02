from __future__ import annotations

from app.agents.state import AnalysisState
from app.models.event import MedicalEvent


def build_timeline(events: list[MedicalEvent]) -> list[MedicalEvent]:
    return sorted(events, key=lambda item: (item.event_date is None, item.event_date, item.id))


def build_timeline_node(state: AnalysisState) -> dict[str, object]:
    events = [MedicalEvent.model_validate(item) for item in state.get("events", [])]
    timeline = build_timeline(events)
    return {
        "timeline": [event.model_dump(mode="json") for event in timeline],
        "completed_nodes": [*state.get("completed_nodes", []), "BUILD_TIMELINE"],
    }

