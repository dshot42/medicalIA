from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.agents.llm import StructuredLLM
from app.agents.nodes.build_timeline import build_timeline_node
from app.agents.nodes.detect_findings import detect_findings_node
from app.agents.nodes.extract_events import extract_events_node
from app.agents.nodes.generate_summary import generate_summary_node
from app.agents.nodes.validate_events import validate_events_node
from app.agents.nodes.verify_sources import flag_uncertain_node, verify_sources_node
from app.agents.state import AnalysisState
from app.storage.repository import Repository

LOGGER = logging.getLogger(__name__)


class AnalysisGraph:
    def __init__(
        self, repository: Repository, checkpoint_path: Path, llm: StructuredLLM
    ) -> None:
        self.repository = repository
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self.graph = self._compile(llm)

    def _load_documents(self, state: AnalysisState) -> dict[str, object]:
        chunks = self.repository.list_chunks(patient_id=state.get("patient_id"))
        return {
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "errors": [],
            "completed_nodes": [*state.get("completed_nodes", []), "LOAD_DOCUMENTS"],
        }

    @staticmethod
    def _route_sources(state: AnalysisState) -> str:
        return "summary" if state.get("all_sources_verified", False) else "uncertain"

    def _compile(self, llm: StructuredLLM):
        workflow = StateGraph(AnalysisState)
        workflow.add_node("load_documents", self._load_documents)
        workflow.add_node("extract_events", extract_events_node)
        workflow.add_node("validate_events", validate_events_node)
        workflow.add_node("build_timeline", build_timeline_node)
        workflow.add_node("detect_findings", detect_findings_node)
        workflow.add_node("verify_sources", verify_sources_node)
        workflow.add_node("flag_uncertain", flag_uncertain_node)
        workflow.add_node("generate_summary", generate_summary_node(llm))
        workflow.add_edge(START, "load_documents")
        workflow.add_edge("load_documents", "extract_events")
        workflow.add_edge("extract_events", "validate_events")
        workflow.add_edge("validate_events", "build_timeline")
        workflow.add_edge("build_timeline", "detect_findings")
        workflow.add_edge("detect_findings", "verify_sources")
        workflow.add_conditional_edges(
            "verify_sources",
            self._route_sources,
            {"summary": "generate_summary", "uncertain": "flag_uncertain"},
        )
        workflow.add_edge("flag_uncertain", "generate_summary")
        workflow.add_edge("generate_summary", END)
        return workflow.compile(checkpointer=self.checkpointer)

    def run(
        self, analysis_id: str | None = None, patient_id: str = "demo_patient"
    ) -> AnalysisState:
        thread_id = analysis_id or str(uuid4())
        initial: AnalysisState = {
            "analysis_id": thread_id,
            "patient_id": patient_id,
            "completed_nodes": [],
            "errors": [],
        }
        print(f"[Analyse documentaire {thread_id}] démarrage.", flush=True)
        LOGGER.info(
            "document_analysis_started",
            extra={"event": "document_analysis_started", "analysis_id": thread_id},
        )
        result: dict[str, object] = dict(initial)
        config = {"configurable": {"thread_id": thread_id}}
        for update in self.graph.stream(initial, config, stream_mode="updates"):
            for node, node_update in update.items():
                if not isinstance(node_update, dict):
                    continue
                result.update(node_update)
                details = {
                    "event": "document_analysis_step",
                    "analysis_id": thread_id,
                    "node": node,
                    "chunk_count": len(result.get("chunks", [])),
                    "event_count": len(result.get("events", [])),
                    "timeline_count": len(result.get("timeline", [])),
                    "finding_count": len(result.get("findings", [])),
                    "error_count": len(result.get("errors", [])),
                }
                print(
                    f"[Analyse documentaire {thread_id}] étape={node} "
                    f"documents_indexés={details['chunk_count']} "
                    f"événements={details['event_count']} "
                    f"chronologie={details['timeline_count']} "
                    f"points_à_clarifier={details['finding_count']} "
                    f"erreurs={details['error_count']}",
                    flush=True,
                )
                LOGGER.info("document_analysis_step", extra=details)
        return AnalysisState(**result)

    def close(self) -> None:
        self._checkpoint_connection.close()

