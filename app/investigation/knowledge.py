from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path

from app.investigation.parsing import normalize
from app.models.investigation import MedicalKnowledgeEvidence, MedicalKnowledgeProfile
from app.rag.qdrant_store import QdrantStore

STOPWORDS = {
    "avec",
    "dans",
    "sans",
    "pour",
    "les",
    "des",
    "une",
    "par",
    "sur",
    "douleur",
    "symptome",
}


def meaningful_tokens(text: str) -> set[str]:
    return {
        token[:8]
        for token in re.findall(r"[a-z0-9]+", normalize(text))
        if len(token) >= 3 and token not in STOPWORDS
    }


def text_match(pattern: str, text: str) -> bool:
    expected = meaningful_tokens(pattern)
    actual = meaningful_tokens(text)
    if not expected:
        return False
    overlap = len(expected & actual)
    return overlap >= min(2, len(expected))


def discriminating_text_match(pattern: str, text: str) -> bool:
    """Use a stricter overlap for specific criteria than for common manifestations."""

    expected = meaningful_tokens(pattern)
    actual = meaningful_tokens(text)
    if not expected:
        return False
    # Require at least three matching terms: two generic tokens such as
    # "IRM cérébrale" cannot establish a disease-specific finding by themselves.
    required = min(len(expected), max(3, math.ceil(len(expected) * 0.5)))
    return len(expected & actual) >= required


def contradiction_match(pattern: str, negative_text: str) -> bool:
    """Require a specific contradiction match, not a shared generic test word."""
    generic = {
        "absence",
        "explicite",
        "document",
        "normal",
        "normale",
        "negatif",
        "negative",
        "pendant",
        "contexte",
    }
    expected = meaningful_tokens(pattern) - generic
    actual = meaningful_tokens(negative_text)
    if not expected:
        return False
    overlap = len(expected & actual)
    return overlap >= min(2, len(expected))


class MedicalKnowledgeService:
    def __init__(self, path: Path, store: QdrantStore) -> None:
        self.path = path
        self.store = store
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.scope_notice = str(payload["scope_notice"])
        self.profiles = [
            MedicalKnowledgeProfile.model_validate(item) for item in payload["profiles"]
        ]
        self.by_name = {profile.disease_name: profile for profile in self.profiles}
        self.by_normalized_name = {
            normalize(name): profile
            for profile in self.profiles
            for name in [profile.disease_name, *profile.aliases]
        }
        self._ensure_indexed()

    def _ensure_indexed(self) -> None:
        records = [
            {
                "id": profile.knowledge_id,
                "text": profile.searchable_text,
                "payload": {
                    **profile.model_dump(mode="json"),
                    "text": profile.searchable_text,
                    "record_type": "medical_knowledge",
                },
            }
            for profile in self.profiles
        ]
        self.store.index_records(records)

    def search(self, query: str, limit: int = 7) -> list[MedicalKnowledgeProfile]:
        vector_results = self.store.search(query, limit=len(self.profiles))
        vector_scores = {
            str(result["disease_name"]): float(result["score"])
            for result in vector_results
        }
        query_tokens = meaningful_tokens(query)

        def score(profile: MedicalKnowledgeProfile) -> tuple[int, float]:
            lexical = len(query_tokens & meaningful_tokens(profile.searchable_text))
            return lexical, vector_scores.get(profile.disease_name, 0.0)

        return sorted(self.profiles, key=score, reverse=True)[:limit]

    def get_profile(self, disease_name: str) -> MedicalKnowledgeProfile:
        return self.by_name[disease_name]

    def match_profile(self, disease_name: str) -> MedicalKnowledgeProfile | None:
        """Return only an explicit disease/alias match, never a guessed nearest disease."""
        return self.by_normalized_name.get(normalize(disease_name))

    @staticmethod
    def to_evidence(profile: MedicalKnowledgeProfile) -> MedicalKnowledgeEvidence:
        statement = (
            f"Manifestations décrites : {', '.join(profile.typical_manifestations)}. "
            f"Critères confirmatoires : "
            f"{', '.join(profile.confirmatory_features) or 'non documentés'}. "
            f"Critères ou résultats discriminants : {', '.join(profile.discriminating_features)}. "
            f"Marqueurs contradictoires : {', '.join(profile.contradiction_markers) or 'non documentés'}. "
            f"Diagnostics différentiels : {', '.join(profile.differential_diagnoses)}. "
            f"Examens de confirmation ou utiles : {', '.join(profile.useful_tests)}."
        )
        return MedicalKnowledgeEvidence(
            knowledge_id=profile.knowledge_id,
            disease_name=profile.disease_name,
            statement=statement,
            evidence_category="reference_profile",
            source_title=profile.source_title,
            source_url=profile.source_url,
            source_organization=profile.source_organization,
            last_reviewed=profile.last_reviewed,
            retrieved_at=date.today(),
        )

