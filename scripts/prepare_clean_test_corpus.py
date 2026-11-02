from __future__ import annotations

import argparse
import json
from pathlib import Path

from qdrant_client import QdrantClient, models

from app.core.config import Settings
from app.storage.repository import Repository


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "data" / "test_corpus_exclusions.json"


def excluded_filenames() -> set[str]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {str(item) for item in payload["excluded_filenames"]}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retire uniquement les documents explicitement exclus d'un patient de test."
    )
    parser.add_argument("--patient-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    repository = Repository(settings.database_path)
    client = QdrantClient(path=str(settings.qdrant_path))
    try:
        excluded_names = excluded_filenames()
        excluded = {item.casefold() for item in excluded_names}
        targets = [
            document
            for document in repository.list_documents(args.patient_id)
            if document.filename.casefold() in excluded
        ]
        result = {
            "patient_id": args.patient_id,
            "dry_run": args.dry_run,
            "targets": [
                {"document_id": item.id, "filename": item.filename}
                for item in targets
            ],
            "deleted": [],
        }
        if not args.dry_run:
            for document in targets:
                client.delete(
                    collection_name=settings.qdrant_collection,
                    points_selector=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchValue(value=document.id),
                            )
                        ]
                    ),
                    wait=True,
                )
                if repository.delete_document(document.id):
                    result["deleted"].append(document.id)
                for path in settings.upload_dir.glob(f"{document.id}.*"):
                    if path.is_file() and path.parent.resolve() == settings.upload_dir.resolve():
                        path.unlink(missing_ok=True)
            remaining_chunks = repository.list_chunks(patient_id=args.patient_id)
            if any(item.filename.casefold() in excluded for item in remaining_chunks):
                raise RuntimeError("Un chunk exclu subsiste dans le dépôt relationnel.")
            remaining = client.count(
                collection_name=settings.qdrant_collection,
                count_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="patient_id",
                            match=models.MatchValue(value=args.patient_id),
                        ),
                        models.FieldCondition(
                            key="filename",
                            match=models.MatchAny(any=list(excluded_names)),
                        ),
                    ]
                ),
                exact=True,
            )
            if remaining.count:
                raise RuntimeError("Un chunk exclu subsiste dans les résultats Qdrant.")
            result["retrieval_verified_clean"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
