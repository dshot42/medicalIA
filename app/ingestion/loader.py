from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader


class InvalidDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedDocument:
    filename: str
    media_type: str
    raw: bytes
    sha256: str
    pages: list[str]

    @property
    def text(self) -> str:
        return "\n\n".join(page for page in self.pages if page).strip()


class DocumentLoader:
    ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown"}
    MEDIA_TYPES = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
    }

    def __init__(self, max_size_bytes: int) -> None:
        self.max_size_bytes = max_size_bytes

    def load_archive(self, filename: str, content: bytes) -> tuple[list[LoadedDocument], list[str]]:
        if Path(filename).suffix.lower() != ".zip":
            raise InvalidDocumentError("L'archive doit être un fichier ZIP.")
        if not content or len(content) > self.max_size_bytes:
            raise InvalidDocumentError("Archive vide ou supérieure à la taille autorisée.")
        try:
            archive = ZipFile(BytesIO(content))
        except BadZipFile as exc:
            raise InvalidDocumentError("Archive ZIP invalide.") from exc

        loaded: list[LoadedDocument] = []
        ignored: list[str] = []
        total_uncompressed = 0
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) > 100:
            raise InvalidDocumentError("L'archive dépasse la limite de 100 fichiers.")
        try:
            for member in members:
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts or member.flag_bits & 0x1:
                    raise InvalidDocumentError("Chemin ou entrée chiffrée interdite dans l'archive.")
                total_uncompressed += member.file_size
                if total_uncompressed > self.max_size_bytes * 10:
                    raise InvalidDocumentError("Le contenu décompressé dépasse la limite autorisée.")
                if path.suffix.lower() not in self.ALLOWED_SUFFIXES:
                    ignored.append(member.filename)
                    continue
                if member.file_size > self.max_size_bytes:
                    raise InvalidDocumentError(
                        f"Le fichier {path.name} dépasse la taille maximale autorisée."
                    )
                extracted = archive.read(member)
                loaded.append(self.load(path.name, extracted))
        except (BadZipFile, RuntimeError) as exc:
            raise InvalidDocumentError("Une entrée de l'archive est illisible.") from exc
        finally:
            archive.close()
        if not loaded:
            raise InvalidDocumentError("L'archive ne contient aucun PDF, TXT ou Markdown valide.")
        return loaded, ignored

    def load(self, filename: str, content: bytes) -> LoadedDocument:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name or safe_name in {".", ".."}:
            raise InvalidDocumentError("Nom de fichier invalide.")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in self.ALLOWED_SUFFIXES:
            raise InvalidDocumentError(
                "Format non pris en charge (PDF, TXT et Markdown uniquement)."
            )
        if not content:
            raise InvalidDocumentError("Le fichier est vide.")
        if len(content) > self.max_size_bytes:
            raise InvalidDocumentError(
                f"Le fichier dépasse la taille maximale de {self.max_size_bytes} octets."
            )
        pages = self._extract_pdf(content) if suffix == ".pdf" else [self._decode_text(content)]
        if not any(page.strip() for page in pages):
            raise InvalidDocumentError(
                "Aucun texte extractible. Les PDF scannés nécessitent un OCR, "
                "non inclus dans ce MVP."
            )
        return LoadedDocument(
            filename=safe_name,
            media_type=self.MEDIA_TYPES[suffix],
            raw=content,
            sha256=hashlib.sha256(content).hexdigest(),
            pages=pages,
        )

    @staticmethod
    def _decode_text(content: bytes) -> str:
        if b"\x00" in content:
            raise InvalidDocumentError("Le fichier texte contient des octets non valides.")
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                text = content.decode(encoding)
                return re.sub(r"\r\n?", "\n", text).strip()
            except UnicodeDecodeError:
                continue
        raise InvalidDocumentError("Encodage texte non pris en charge.")

    @staticmethod
    def _extract_pdf(content: bytes) -> list[str]:
        try:
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise InvalidDocumentError("Les PDF chiffrés ne sont pas pris en charge.")
            return [(page.extract_text() or "").strip() for page in reader.pages]
        except InvalidDocumentError:
            raise
        except Exception as exc:
            raise InvalidDocumentError("PDF invalide ou illisible.") from exc

