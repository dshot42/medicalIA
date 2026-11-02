from __future__ import annotations

from app.agents.graph import AnalysisGraph
from app.models.event import MedicalEvent
from app.models.finding import Finding, SourceReference
from app.storage.repository import Repository


class AnalysisService:
    def __init__(self, repository: Repository, graph: AnalysisGraph) -> None:
        self.repository = repository
        self.graph = graph

    def run(
        self, analysis_id: str | None = None, patient_id: str = "demo_patient"
    ) -> dict[str, object]:
        state = self.graph.run(analysis_id, patient_id)
        events = [MedicalEvent.model_validate(item) for item in state.get("timeline", [])]
        findings = [Finding.model_validate(item) for item in state.get("findings", [])]
        self.repository.replace_analysis(events, findings)
        return {
            "analysis_id": state["analysis_id"],
            "summary": state.get("summary", ""),
            "events": events,
            "findings": findings,
            "errors": state.get("errors", []),
            "completed_nodes": state.get("completed_nodes", []),
        }

    def timeline(self, patient_id: str | None = None) -> list[MedicalEvent]:
        return self.repository.list_events(patient_id)

    def findings(self, patient_id: str | None = None) -> list[Finding]:
        return self.repository.list_findings(patient_id)

    def event_sources(
        self, event_id: str, patient_id: str | None = None
    ) -> list[SourceReference] | None:
        event = self.repository.get_event(event_id, patient_id)
        if event is None:
            return None
        chunk = self.repository.get_chunk(event.source_chunk_id, event.patient_id)
        if chunk is None or event.source_quote not in chunk.text:
            return []
        return [
            SourceReference(
                document_id=event.source_document_id,
                chunk_id=event.source_chunk_id,
                filename=chunk.filename,
                page=chunk.page,
                quote=event.source_quote,
            )
        ]

