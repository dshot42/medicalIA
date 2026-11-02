from __future__ import annotations


def test_qdrant_search_metadata_filter_and_references(container) -> None:
    first, _ = container.document_service.import_document(
        "fatigue.txt", b"Fatigue persistante rapportee pendant la consultation."
    )
    second, _ = container.document_service.import_document(
        "controle.txt", b"Controle biologique recommande dans trois mois."
    )
    results = container.retriever.search("fatigue persistante", limit=5)
    assert results
    assert {"document_id", "filename", "page", "chunk_id", "text", "score"} <= results[0].keys()
    filtered = container.retriever.search("controle", document_id=second.id)
    assert filtered
    assert all(item["document_id"] == second.id for item in filtered)
    assert all(item["document_id"] != first.id for item in filtered)

