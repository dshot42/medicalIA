from __future__ import annotations

import pytest
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from app.ingestion.chunker import chunk_pages
from app.ingestion.loader import DocumentLoader, InvalidDocumentError


def test_text_extraction_and_stable_traceable_chunks() -> None:
    loader = DocumentLoader(max_size_bytes=10_000)
    loaded = loader.load("consultation.txt", b"Consultation le 12 mars 2024.\nFatigue.")
    first = chunk_pages("doc_001", loaded.filename, loaded.pages, chunk_size=25, overlap=5)
    second = chunk_pages("doc_001", loaded.filename, loaded.pages, chunk_size=25, overlap=5)
    assert first == second
    assert first[0].document_id == "doc_001"
    assert first[0].filename == "consultation.txt"
    assert first[0].page == 1
    assert first[0].text


@pytest.mark.parametrize("filename", ["../secret.txt", "folder/file.md", "malware.exe"])
def test_rejects_unsafe_or_unsupported_names(filename: str) -> None:
    with pytest.raises(InvalidDocumentError):
        DocumentLoader(1000).load(filename, b"content")


def test_rejects_oversized_document() -> None:
    with pytest.raises(InvalidDocumentError, match="taille maximale"):
        DocumentLoader(3).load("file.txt", b"four")


def test_zip_archive_preserves_supported_documents_and_reports_ignored_files() -> None:
    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("records/visit.txt", "Consultation du 12 mars 2024.")
        archive.writestr("labs/results.md", "Résultat biologique fictif.")
        archive.writestr("readme.exe", "unsupported")
    loaded, ignored = DocumentLoader(10_000).load_archive("records.zip", archive_bytes.getvalue())

    assert {item.filename for item in loaded} == {"visit.txt", "results.md"}
    assert loaded[0].text or loaded[1].text
    assert ignored == ["readme.exe"]


def test_zip_archive_rejects_path_traversal() -> None:
    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w") as archive:
        archive.writestr("../outside.txt", "must not escape")
    with pytest.raises(InvalidDocumentError, match="Chemin"):
        DocumentLoader(10_000).load_archive("records.zip", archive_bytes.getvalue())


def test_deduplication_and_effective_deletion(container) -> None:
    content = b"Date du document : 12 mars 2024. Consultation fictive."
    document, duplicate = container.document_service.import_document("one.txt", content)
    same, duplicate_again = container.document_service.import_document("copy.txt", content)
    assert duplicate is False
    assert duplicate_again is True
    assert same.id == document.id
    assert len(container.repository.list_documents()) == 1
    assert container.vector_store.count_document(document.id) > 0

    assert container.document_service.delete_document(document.id) is True
    assert container.repository.get_document(document.id) is None
    assert container.vector_store.count_document(document.id) == 0


def test_created_patient_and_archive_documents_are_saved_and_indexed(container) -> None:
    assert container.repository.create_patient("client-demo-17") is True
    assert container.repository.create_patient("client-demo-17") is False
    assert "client-demo-17" in container.repository.list_patients("demo-17")

    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("visit.txt", "Consultation du 12 mars 2024. Suivi fictif.")
        archive.writestr("results.md", "Résultat fictif de laboratoire.")
    imported, ignored = container.document_service.import_archive(
        "client-records.zip", archive_bytes.getvalue(), "client-demo-17"
    )

    assert len(imported) == 2
    assert ignored == []
    assert len(container.repository.list_documents("client-demo-17")) == 2
    assert all(
        container.vector_store.count_document(document.id) > 0
        for document, _ in imported
    )

