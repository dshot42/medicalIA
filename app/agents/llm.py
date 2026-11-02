from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, Field

from app.agents.local_gguf import LocalGGUFClient
from app.core.config import Settings


class SummaryOutput(BaseModel):
    summary: str = Field(min_length=10, max_length=3000)


class StructuredLLM:
    """Client minimal, opt-in, avec validation Pydantic de la réponse."""

    SYSTEM_PROMPT = (
        "Tu synthétises uniquement des observations documentaires médicales fictives ou fournies "
        "par l'utilisateur. N'établis jamais de diagnostic ni de causalité. "
        "Mentionne explicitement "
        "les incertitudes. Réponds en JSON avec une unique clé 'summary'."
    )

    def __init__(
        self, settings: Settings, local_gguf: LocalGGUFClient | None = None
    ) -> None:
        self.settings = settings
        self.local_gguf = local_gguf

    def summarize(self, facts: dict[str, object]) -> SummaryOutput | None:
        if self.settings.llm_provider == "disabled":
            return None
        user_prompt = json.dumps(facts, ensure_ascii=False)
        if self.settings.llm_provider == "gguf":
            if self.local_gguf is None or not self.local_gguf.enabled:
                return None
            return self.local_gguf.generate_structured(
                self.SYSTEM_PROMPT, facts, SummaryOutput, max_tokens=256
            )
        if self.settings.llm_provider == "ollama":
            response = httpx.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": self.settings.llm_model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
        else:
            response = httpx.post(
                f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json={
                    "model": self.settings.llm_model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        return SummaryOutput.model_validate_json(content)

