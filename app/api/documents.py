from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status

from app.container import get_container
from app.ingestion.loader import InvalidDocumentError
from app.models.document import DocumentDetail, DocumentRecord

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def import_documents(
    files: list[UploadFile] = File(...),
    patient_id: str = Form(default="demo_patient", min_length=1, max_length=100),
) -> list[dict[str, object]]:
    if not files:
        raise HTTPException(status_code=400, detail="Au moins un fichier est requis.")
    container = get_container()
    maximum = container.settings.max_file_size_mb * 1024 * 1024
    results: list[dict[str, object]] = []
    for upload in files:
        content = await upload.read(maximum + 1)
        try:
            filename = upload.filename or ""
            if Path(filename).suffix.lower() == ".zip":
                imported, ignored = container.document_service.import_archive(
                    filename, content, patient_id
                )
                results.extend(
                    {
                        "document": document.model_dump(mode="json"),
                        "duplicate": duplicate,
                        "ignored_files": ignored,
                    }
                    for document, duplicate in imported
                )
                continue
            document, duplicate = container.document_service.import_document(
                filename, content, patient_id
            )
        except InvalidDocumentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        results.append({"document": document.model_dump(mode="json"), "duplicate": duplicate})
    return results


@router.get("", response_model=list[DocumentRecord])
def list_documents(patient_id: str | None = Query(default=None)) -> list[DocumentRecord]:
    return get_container().document_service.list_documents(patient_id)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, patient_id: str | None = None) -> DocumentDetail:
    document = get_container().document_service.get_document(document_id, patient_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return document


@router.delete(
    "/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_document(document_id: str) -> Response:
    if not get_container().document_service.delete_document(document_id):
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

