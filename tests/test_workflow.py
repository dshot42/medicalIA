from __future__ import annotations

from app.models.finding import FindingCategory


def test_langgraph_complete_workflow(container, sample_dir) -> None:
    for path in sorted(sample_dir.glob("*")):
        if path.suffix in {".txt", ".md"}:
            container.document_service.import_document(path.name, path.read_bytes())
    result = container.analysis_service.run("workflow-test")
    assert result["completed_nodes"] == [
        "LOAD_DOCUMENTS",
        "EXTRACT_EVENTS",
        "VALIDATE_EVENTS",
        "BUILD_TIMELINE",
        "DETECT_FINDINGS",
        "VERIFY_SOURCES",
        "GENERATE_SUMMARY",
    ]
    assert len(result["events"]) >= 12
    categories = {finding.category for finding in result["findings"]}
    assert categories == set(FindingCategory)
    assert all(finding.sources_verified for finding in result["findings"])
    assert all(finding.verification_message for finding in result["findings"])

