from __future__ import annotations

import json
from pathlib import Path

from app.container import get_container


def main() -> None:
    container = get_container()
    root = Path(__file__).parents[1] / "data" / "patients"
    exclusions_path = root.parent / "test_corpus_exclusions.json"
    exclusions = {
        str(item).casefold()
        for item in json.loads(exclusions_path.read_text(encoding="utf-8"))[
            "excluded_filenames"
        ]
    }
    imported = 0
    duplicates = 0
    for patient_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for path in sorted(patient_dir.glob("*")):
            if path.suffix not in {".txt", ".md"}:
                continue
            if path.name.casefold() in exclusions:
                continue
            _, duplicate = container.document_service.import_document(
                path.name, path.read_bytes(), patient_dir.name
            )
            imported += int(not duplicate)
            duplicates += int(duplicate)
    print(
        f"Dossiers cliniques fictifs chargés : {imported} import(s), "
        f"{duplicates} doublon(s), patients={container.repository.list_patients()}."
    )
    container.close()
    get_container.cache_clear()


if __name__ == "__main__":
    main()

