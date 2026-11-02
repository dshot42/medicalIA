from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.container import get_container

mcp = FastMCP(
    "MedConnect AI",
    instructions=(
        "Consultation locale de documents médicaux importés. Les résultats sont documentaires, "
        "ne constituent pas un diagnostic et ne démontrent aucune causalité."
    ),
)


@mcp.tool()
def search_medical_documents(
    query: str, patient_id: str = "demo_patient", limit: int = 5
) -> list[dict[str, object]]:
    """Recherche sémantique locale avec références exactes de chunks."""
    return get_container().retriever.search(query, limit, patient_id=patient_id)


@mcp.tool()
def get_document(
    document_id: str, patient_id: str = "demo_patient"
) -> dict[str, object]:
    """Retourne le document et ses chunks traçables."""
    document = get_container().document_service.get_document(document_id, patient_id)
    if document is None:
        return {"error": "Document introuvable."}
    return document.model_dump(mode="json")


@mcp.tool()
def get_medical_timeline(patient_id: str = "demo_patient") -> list[dict[str, object]]:
    """Retourne la chronologie documentaire, sans interprétation diagnostique."""
    return [
        event.model_dump(mode="json")
        for event in get_container().analysis_service.timeline(patient_id)
    ]


@mcp.tool()
def get_analysis_findings(patient_id: str = "demo_patient") -> list[dict[str, object]]:
    """Retourne les constats vérifiés, leurs incertitudes et leurs sources."""
    return [
        finding.model_dump(mode="json")
        for finding in get_container().analysis_service.findings(patient_id)
    ]


@mcp.tool()
def get_event_sources(
    event_id: str, patient_id: str = "demo_patient"
) -> list[dict[str, object]] | dict[str, str]:
    """Retourne le ou les passages sources exacts d'un événement."""
    sources = get_container().analysis_service.event_sources(event_id, patient_id)
    if sources is None:
        return {"error": "Événement introuvable."}
    return [source.model_dump(mode="json") for source in sources]


@mcp.tool()
def search_patient_evidence(
    patient_id: str, query: str, limit: int = 8
) -> list[dict[str, object]]:
    """Recherche exclusivement dans le dossier du patient demandé."""
    return get_container().retriever.search(query, limit, patient_id=patient_id)


@mcp.tool()
def search_medical_knowledge(query: str, limit: int = 7) -> list[dict[str, object]]:
    """Recherche dans le référentiel médical général, séparé des dossiers patients."""
    return [
        profile.model_dump(mode="json")
        for profile in get_container().knowledge_service.search(query, limit)
    ]


@mcp.tool()
def get_patient_timeline(patient_id: str) -> list[dict[str, object]]:
    """Chronologie strictement filtrée par patient."""
    return [
        event.model_dump(mode="json")
        for event in get_container().analysis_service.timeline(patient_id)
    ]


@mcp.tool()
def get_clinical_investigation(
    patient_id: str, investigation_id: str
) -> dict[str, object]:
    """Restitue une investigation historique strictement filtrée par patient."""
    result = get_container().investigation_service.get(investigation_id, patient_id)
    return result or {"error": "Investigation introuvable."}


@mcp.tool()
def get_hypothesis_evidence(
    patient_id: str, investigation_id: str, hypothesis_id: str
) -> dict[str, object]:
    """Retourne séparément preuves patient et connaissances d'une hypothèse."""
    result = get_container().investigation_service.get_hypothesis_evidence(
        investigation_id, hypothesis_id, patient_id
    )
    return result or {"error": "Investigation ou hypothèse introuvable."}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

