from __future__ import annotations

from app.agents.state import AnalysisState
from app.models.document import DocumentChunk
from app.models.finding import Finding, UncertaintyLevel

INSUFFICIENT_MESSAGE = "Informations insuffisantes pour confirmer ce rapprochement."


def verify_finding_sources(
    findings: list[Finding], chunks: list[DocumentChunk]
) -> list[Finding]:
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    verified: list[Finding] = []
    for finding in findings:
        valid = bool(finding.sources)
        for source in finding.sources:
            chunk = chunks_by_id.get(source.chunk_id)
            if (
                chunk is None
                or chunk.document_id != source.document_id
                or source.quote not in chunk.text
            ):
                valid = False
                break
        updated = finding.model_copy(deep=True)
        updated.sources_verified = valid
        if valid:
            updated.verification_message = "Références documentaires vérifiées."
        else:
            updated.verification_message = INSUFFICIENT_MESSAGE
            updated.uncertainty = UncertaintyLevel.HIGH
            updated.confidence = min(updated.confidence, 0.35)
        verified.append(updated)
    return verified


def verify_sources_node(state: AnalysisState) -> dict[str, object]:
    chunks = [DocumentChunk.model_validate(item) for item in state.get("chunks", [])]
    findings = [Finding.model_validate(item) for item in state.get("findings", [])]
    verified = verify_finding_sources(findings, chunks)
    return {
        "findings": [finding.model_dump(mode="json") for finding in verified],
        "all_sources_verified": all(finding.sources_verified for finding in verified),
        "completed_nodes": [*state.get("completed_nodes", []), "VERIFY_SOURCES"],
    }


def flag_uncertain_node(state: AnalysisState) -> dict[str, object]:
    findings = [Finding.model_validate(item) for item in state.get("findings", [])]
    for finding in findings:
        if not finding.sources_verified:
            finding.uncertainty = UncertaintyLevel.HIGH
            finding.verification_message = INSUFFICIENT_MESSAGE
    return {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "completed_nodes": [*state.get("completed_nodes", []), "FLAG_UNCERTAIN"],
    }

