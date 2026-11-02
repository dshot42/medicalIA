from __future__ import annotations

from pathlib import Path

from app.container import get_container


def main() -> None:
    container = get_container()
    sample_dir = Path(__file__).parents[1] / "data" / "samples"
    imported = 0
    duplicates = 0
    for path in sorted(sample_dir.glob("*")):
        if path.suffix not in {".txt", ".md"}:
            continue
        _, duplicate = container.document_service.import_document(path.name, path.read_bytes())
        imported += int(not duplicate)
        duplicates += int(duplicate)
    result = container.analysis_service.run()
    print(
        f"Démonstration chargée : {imported} import(s), {duplicates} doublon(s), "
        f"{len(result['events'])} événement(s), {len(result['findings'])} constat(s)."
    )
    print(result["summary"])
    container.close()
    get_container.cache_clear()


if __name__ == "__main__":
    main()

