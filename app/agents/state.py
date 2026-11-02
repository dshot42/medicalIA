from __future__ import annotations

from typing import TypedDict


class AnalysisState(TypedDict, total=False):
    analysis_id: str
    patient_id: str
    chunks: list[dict[str, object]]
    events: list[dict[str, object]]
    timeline: list[dict[str, object]]
    findings: list[dict[str, object]]
    errors: list[str]
    summary: str
    all_sources_verified: bool
    completed_nodes: list[str]

