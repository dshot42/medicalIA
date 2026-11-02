from __future__ import annotations

import asyncio

from app.mcp.server import mcp


def test_mcp_exposes_real_document_tools() -> None:
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert names == {
        "search_medical_documents",
        "get_document",
        "get_medical_timeline",
        "get_analysis_findings",
        "get_event_sources",
        "search_patient_evidence",
        "search_medical_knowledge",
        "get_patient_timeline",
        "get_clinical_investigation",
        "get_hypothesis_evidence",
    }

