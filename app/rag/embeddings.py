from __future__ import annotations

import hashlib
import math
from typing import Protocol


class Embedder(Protocol):
    @property
    def dimension(self) -> int: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Embeddings réellement locaux ; aucun texte n'est envoyé à un service distant."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)

    @property
    def dimension(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [vector.tolist() for vector in vectors]


class HashingEmbedder:
    """Embeddings hors ligne déterministes réservés aux tests et au dépannage explicite."""

    def __init__(self, dimension: int = 128) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        words = text.lower().split()
        for word in words:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += -1.0 if digest[4] & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def create_embedder(model_name: str) -> Embedder:
    if model_name == "hashing":
        return HashingEmbedder()
    return SentenceTransformerEmbedder(model_name)

