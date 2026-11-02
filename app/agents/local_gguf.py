from __future__ import annotations

import gc
import json
import logging
import re
import threading
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.investigation.parsing import normalize
from app.models.investigation import (
    ClinicalConceptType,
    ConfidenceLevel,
    DiagnosticStatus,
    DiagnosticUncertainty,
    EvidenceStrengthClass,
    MedicalKnowledgeProfile,
    ParsedConsultation,
    PatientEvidence,
    SymptomObservation,
    SymptomStatus,
)

LOGGER = logging.getLogger(__name__)
StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class LocalSymptomCandidate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    status: SymptomStatus
    source_quote: str = Field(min_length=2, max_length=500)
    temporal_pattern: str | None = Field(default=None, max_length=120)
    severity: str | None = Field(default=None, max_length=120)


class LocalConsultationExtraction(BaseModel):
    symptoms: list[LocalSymptomCandidate] = Field(default_factory=list, max_length=30)
    duration: str | None = Field(default=None, max_length=120)
    duration_source_quote: str | None = Field(default=None, max_length=500)
    severity: str | None = Field(default=None, max_length=120)
    severity_source_quote: str | None = Field(default=None, max_length=500)
    missing_details: list[str] = Field(default_factory=list, max_length=20)


class GroundedHypothesisChoice(BaseModel):
    disease_name: str = Field(min_length=2, max_length=200)
    relevant_patient_evidence_ids: list[str] = Field(default_factory=list, max_length=30)


class GroundedHypothesisSelection(BaseModel):
    ordered_disease_names: list[str] = Field(
        default_factory=list, min_length=1, max_length=7
    )


class LLMOnlyHypothesisCandidate(BaseModel):
    disease_name: str = Field(min_length=2, max_length=200)
    supporting_symptom_quotes: list[str] = Field(min_length=1, max_length=8)
    missing_information: list[str] = Field(default_factory=list, max_length=12)
    reasoning_summary: str = Field(min_length=10, max_length=800)


class LLMOnlyDifferentialOutput(BaseModel):
    hypotheses: list[LLMOnlyHypothesisCandidate] = Field(min_length=3, max_length=7)


class QwenGroundedHypothesisCandidate(BaseModel):
    disease_name: str = Field(min_length=2, max_length=200)
    concept_type: ClinicalConceptType = ClinicalConceptType.DISEASE
    reasoning_summary: str = Field(min_length=10, max_length=1000)
    targeted_search_terms: list[str] = Field(default_factory=list, min_length=1, max_length=5)


class QwenGroundedDifferentialOutput(BaseModel):
    hypotheses: list[QwenGroundedHypothesisCandidate] = Field(min_length=3, max_length=8)


class QwenHypothesisReview(BaseModel):
    hypothesis_id: str = ""
    disease_name: str = Field(min_length=2, max_length=200)
    evidence_strength: ConfidenceLevel
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    discriminative_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    chronology_assessed: bool
    reasoning_summary: str = Field(min_length=20, max_length=1600)
    validated_relations: list[dict[str, object]] = Field(default_factory=list, max_length=50)
    validated_diagnostic_status: DiagnosticStatus = DiagnosticStatus.INSUFFICIENT_EVIDENCE
    validated_evidence_strength: EvidenceStrengthClass = EvidenceStrengthClass.INSUFFICIENT
    validated_uncertainty: DiagnosticUncertainty = DiagnosticUncertainty.UNDETERMINED


class QwenComparativeHypothesisAssessment(BaseModel):
    hypothesis_id: str
    diagnostic_status: DiagnosticStatus
    evidence_strength: EvidenceStrengthClass
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    discriminative_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    alternative_diagnoses: list[str] = Field(default_factory=list, max_length=8)
    comparative_justification: str = Field(min_length=10, max_length=1200)
    uncertainty: DiagnosticUncertainty
    reference_ids: list[str] = Field(default_factory=list, max_length=40)
    relative_position: int = Field(ge=0, le=8)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class QwenComparativeRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ordered_hypothesis_ids: list[str] = Field(default_factory=list, max_length=8)
    # Kept for reading older persisted responses and test doubles.
    ordered_disease_names: list[str] = Field(default_factory=list, max_length=8)
    assessments: list[QwenComparativeHypothesisAssessment] = Field(
        default_factory=list, max_length=8
    )
    can_prioritize_one: bool
    conclusion: str = Field(min_length=10, max_length=1000)


def _quote_in_source(quote: str | None, source: str) -> bool:
    if not quote:
        return False
    return normalize(quote).strip() in normalize(source)


def _has_meaningful_overlap(name: str, quote: str) -> bool:
    name_terms = {term for term in re.findall(r"\w+", normalize(name)) if len(term) >= 4}
    quote_terms = set(re.findall(r"\w+", normalize(quote)))
    return bool(name_terms & quote_terms)


def _quote_is_negative(quote: str) -> bool:
    return bool(
        re.search(
            r"\b(?:pas de|sans|aucun(?:e)?|nie|absence de|ne rapporte pas)\b",
            normalize(quote),
        )
    )


class LocalGGUFClient:
    """Adaptateur local et paresseux pour un GGUF via llama.cpp.

    Les sorties sont contraintes en JSON, validées par Pydantic puis revalidées
    contre les textes ou identifiants fournis. Aucun appel réseau n'est effectué.
    """

    EXTRACTION_SYSTEM_PROMPT = (
        "Tu extrais fidèlement des symptômes d'un texte de consultation en français. "
        "N'invente rien. Toute caractéristique clinique doit avoir une citation exacte du texte. "
        "Distingue present, denied et not_documented. Une information absente n'est jamais denied. "
        "Réponds uniquement avec l'objet JSON demandé. /no_think"
    )
    HYPOTHESIS_SYSTEM_PROMPT = (
        "Tu aides à ordonner des hypothèses de travail pour un professionnel de santé. "
        "Tu dois ordonner exclusivement les noms de maladies du référentiel fourni. "
        "N'ajoute aucune maladie. N'établis aucun diagnostic, "
        "aucun pourcentage et aucune recommandation de traitement. Réponds uniquement en JSON. "
        "/no_think"
    )
    LLM_ONLY_SYSTEM_PROMPT = (
        "Tu produis un diagnostic différentiel prudent à partir du seul texte de symptômes fourni. "
        "Génère exactement 3 hypothèses de travail, jamais un diagnostic confirmé. Pour chacune, "
        "cite exactement un fragment de symptôme et liste les informations "
        "manquantes. N'invente aucun examen, antécédent, résultat ou négation. Ne donne aucun "
        "pourcentage, traitement ou modification de prescription. Garde chaque justification "
        "à une phrase courte. Réponds uniquement en JSON. "
        "/no_think"
    )
    QWEN_GROUNDED_DIFFERENTIAL_PROMPT = (
        "Tu proposes des pistes étiologiques pour un professionnel de santé. "
        "Distingue strictement une constatation (symptôme ou anomalie biologique) d'une maladie "
        "ou d'une étiologie. Une constatation ne doit jamais être proposée comme diagnostic. "
        "À partir du motif, des données patient et des fiches médicales indépendantes fournies, "
        "génère de 3 à 7 maladies ou étiologies distinctes si les données le permettent. Inclus "
        "maladies fréquentes, maladies rares seulement si des indices les justifient, et urgences "
        "à ne pas manquer. Ne fusionne pas plusieurs maladies sous un seul nom. Les termes de "
        "recherche servent uniquement à retrouver des passages du dossier patient. Indique "
        "concept_type=disease ou etiology. N'invente aucun fait patient, pourcentage ou "
        "traitement. "
        "Réponds uniquement avec l'objet JSON demandé. /no_think"
    )
    QWEN_HYPOTHESIS_REVIEW_PROMPT = (
        "Tu examines contradictoirement une seule hypothèse à partir du dossier patient et des "
        "fiches médicales indépendantes fournies. Cite uniquement des "
        "evidence_id fournis. Une information absente n'est jamais une preuve négative. Distingue "
        "arguments favorables, contradictions explicites, indices discriminants, chronologie et "
        "informations manquantes. N'invente aucun critère, résultat, pourcentage ou source. La "
        "force doit être une des valeurs exactes de l'énumération fournie. Une proposition sera "
        "validée indépendamment; reste prudent. Réponds uniquement en JSON. /no_think"
    )
    QWEN_COMPARISON_PROMPT = (
        "Compare les hypothèses exclusivement à partir des relations déjà validées. Pour chaque "
        "hypothèse, propose un statut documentaire, une force de preuve, les identifiants de "
        "preuves favorables, défavorables et discriminantes, les informations manquantes, les "
        "alternatives, une justification comparative, l'incertitude et les références. Hiérarchie "
        "obligatoire: CONFIRMATORY prime sur HIGHLY_DISCRIMINATING, qui prime sur "
        "COMPATIBLE_NON_SPECIFIC. Une accumulation de symptômes non spécifiques ne prime jamais "
        "sur une preuve confirmatoire vérifiée. DOCUMENTED_CONFIRMED exige au moins une relation "
        "CONFIRMATORY validée. Ordonne uniquement les hypothesis_id fournis, sans en ajouter. "
        "Les ex aequo utilisent la même relative_position. Si les preuves ne permettent pas de "
        "départager, can_prioritize_one=false et uncertainty=UNDETERMINED. N'invente aucune "
        "preuve ni référence et ne produis aucun pourcentage. /no_think"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: object | None = None
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self.settings.llm_provider == "gguf" and self.settings.gguf_model_path.is_file()

    @property
    def model_identifier(self) -> str:
        return self.settings.gguf_model_path.name

    def _get_model(self) -> object:
        if not self.enabled:
            raise RuntimeError("Le fournisseur GGUF local n'est pas disponible.")
        with self._lock:
            if self._model is None:
                from llama_cpp import Llama

                self._model = Llama(
                    model_path=str(self.settings.gguf_model_path),
                    n_ctx=self.settings.gguf_context_size,
                    n_threads=self.settings.gguf_threads,
                    n_gpu_layers=self.settings.gguf_gpu_layers,
                    seed=42,
                    verbose=False,
                )
                LOGGER.info(
                    "local_gguf_loaded",
                    extra={
                        "model": self.model_identifier,
                        "provider": "llama.cpp",
                        "context_size": self.settings.gguf_context_size,
                        "gpu_layers": self.settings.gguf_gpu_layers,
                    },
                )
            return self._model

    @staticmethod
    def _json_content(content: str) -> str:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Le modèle local n'a pas renvoyé d'objet JSON.")
        return cleaned[start : end + 1]

    @classmethod
    def _validate_json_content(
        cls, content: str, output_model: type[StructuredOutput]
    ) -> StructuredOutput:
        try:
            return output_model.model_validate_json(cls._json_content(content))
        except ValueError as first_error:
            cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            start = cleaned.find("{")
            if start < 0:
                raise first_error
            repaired = cleaned[start:]
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
            repaired += "]" * max(0, repaired.count("[") - repaired.count("]"))
            repaired += "}" * max(0, repaired.count("{") - repaired.count("}"))
            return output_model.model_validate_json(repaired)

    def generate_structured(
        self,
        system_prompt: str,
        user_payload: dict[str, object],
        output_model: type[StructuredOutput],
        *,
        max_tokens: int | None = None,
        json_contract: str | None = None,
        seed: int = 42,
    ) -> StructuredOutput:
        model = self._get_model()
        schema = output_model.model_json_schema()
        prompt = (
            "Données d'entrée JSON:\n"
            + json.dumps(user_payload, ensure_ascii=False, default=str)
            + (
                "\nFormat JSON de sortie à respecter:\n" + json_contract
                if json_contract
                else "\nSchéma JSON de sortie:\n" + json.dumps(schema, ensure_ascii=False)
            )
        )
        try:
            with self._lock:
                response = model.create_chat_completion(  # type: ignore[attr-defined]
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    # llama.cpp's schema-to-grammar conversion can exceed its grammar
                    # complexity limit for nested Pydantic schemas. Keep JSON mode and
                    # validate the complete result against Pydantic immediately after.
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    seed=seed,
                    top_k=1,
                    top_p=1.0,
                    max_tokens=max_tokens or self.settings.gguf_max_tokens,
                )
        except Exception as exc:
            LOGGER.warning(
                "local_gguf_generation_failed",
                extra={
                    "event": "local_gguf_generation_failed",
                    "operation": output_model.__name__,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        content = response["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Réponse vide du modèle GGUF local.")
        try:
            return self._validate_json_content(content, output_model)
        except (ValueError, KeyError, TypeError) as exc:
            LOGGER.warning(
                "local_gguf_validation_failed",
                extra={
                    "event": "local_gguf_validation_failed",
                    "operation": output_model.__name__,
                    "error_type": type(exc).__name__,
                },
            )
            raise

    def parse_consultation(
        self, text: str, deterministic_fallback: ParsedConsultation
    ) -> ParsedConsultation:
        extracted = self.generate_structured(
            self.EXTRACTION_SYSTEM_PROMPT,
            {"consultation_text": text},
            LocalConsultationExtraction,
            max_tokens=1000,
            json_contract=(
                '{"symptoms":[{"name":"symptôme explicitement mentionné",'
                '"status":"present|denied|not_documented",'
                '"source_quote":"citation exacte du texte",'
                '"temporal_pattern":null,"severity":null}],'
                '"duration":null,"duration_source_quote":null,'
                '"severity":null,"severity_source_quote":null,'
                '"missing_details":["information clinique manquante"]}\n'
                "Utilise [] si aucun symptôme explicite n'est décrit. "
                "N'ajoute aucun autre champ et ne recopie pas ce contrat."
            ),
        )
        observations = {
            (normalize(item.name), item.status): item
            for item in deterministic_fallback.symptoms
        }
        for candidate in extracted.symptoms:
            quote = candidate.source_quote.strip()
            if not _quote_in_source(quote, text) or not _has_meaningful_overlap(
                candidate.name, quote
            ):
                continue
            negative = _quote_is_negative(quote)
            if candidate.status == SymptomStatus.DENIED and not negative:
                continue
            if candidate.status == SymptomStatus.PRESENT and negative:
                continue
            observation = SymptomObservation(
                name=candidate.name.strip(),
                status=candidate.status,
                temporal_pattern=candidate.temporal_pattern,
                severity=candidate.severity,
                source_text=quote,
            )
            observations[(normalize(observation.name), observation.status)] = observation

        duration = deterministic_fallback.duration
        if (
            extracted.duration
            and _quote_in_source(extracted.duration_source_quote, text)
            and normalize(extracted.duration)
            in normalize(extracted.duration_source_quote or "")
        ):
            duration = extracted.duration
        severity = deterministic_fallback.severity
        if (
            extracted.severity
            and _quote_in_source(extracted.severity_source_quote, text)
            and normalize(extracted.severity)
            in normalize(extracted.severity_source_quote or "")
        ):
            severity = extracted.severity
        missing = list(
            dict.fromkeys(
                [
                    *deterministic_fallback.missing_details,
                    *[item.strip() for item in extracted.missing_details if item.strip()],
                ]
            )
        )
        return ParsedConsultation(
            symptoms=list(observations.values()),
            duration=duration,
            severity=severity,
            missing_details=missing,
            not_documented=missing,
        )

    def prioritize_hypotheses(
        self,
        parsed: ParsedConsultation,
        facts: list[PatientEvidence],
        profiles: list[MedicalKnowledgeProfile],
    ) -> list[GroundedHypothesisChoice]:
        selection = self.generate_structured(
            self.HYPOTHESIS_SYSTEM_PROMPT,
            {
                "consultation": parsed.model_dump(mode="json"),
                "patient_evidence": [
                    {
                        "type": item.type,
                        "description": item.description,
                    }
                    for item in facts[:20]
                ],
                "allowed_knowledge_profiles": [
                    {
                        "disease_name": item.disease_name,
                        "typical_manifestations": item.typical_manifestations,
                        "discriminating_features": item.discriminating_features,
                        "useful_tests": item.useful_tests,
                    }
                    for item in profiles
                ],
            },
            GroundedHypothesisSelection,
            max_tokens=160,
        )
        allowed_diseases = {item.disease_name.casefold(): item.disease_name for item in profiles}
        result: list[GroundedHypothesisChoice] = []
        seen: set[str] = set()
        for disease_name in selection.ordered_disease_names:
            canonical = allowed_diseases.get(disease_name.casefold())
            if canonical is None or canonical in seen:
                continue
            seen.add(canonical)
            result.append(
                GroundedHypothesisChoice(
                    disease_name=canonical,
                    relevant_patient_evidence_ids=[],
                )
            )
        return result

    def generate_llm_only_differential(
        self, consultation_text: str
    ) -> list[LLMOnlyHypothesisCandidate]:
        output = self.generate_structured(
            self.LLM_ONLY_SYSTEM_PROMPT,
            {"consultation_symptoms_only": consultation_text},
            LLMOnlyDifferentialOutput,
            max_tokens=1800,
            json_contract=(
                '{"hypotheses":[{"disease_name":"nom de l’hypothèse",'
                '"supporting_symptom_quotes":["citation exacte du texte fourni"],'
                '"missing_information":["information manquante"],'
                '"reasoning_summary":"une phrase courte"}]}\n'
                "Produis exactement 3 hypothèses. Une citation exacte et une phrase courte "
                "par hypothèse. N’ajoute aucun autre champ."
            ),
        )
        result: list[LLMOnlyHypothesisCandidate] = []
        seen: set[str] = set()
        prohibited = re.compile(
            r"(?:\b\d+(?:[.,]\d+)?\s*%|\bprescri|\bdosage\b|\bposologie\b)",
            re.IGNORECASE,
        )
        for candidate in output.hypotheses:
            name_key = normalize(candidate.disease_name).strip()
            if not name_key or name_key in seen or prohibited.search(candidate.reasoning_summary):
                continue
            quotes = list(
                dict.fromkeys(
                    quote.strip()
                    for quote in candidate.supporting_symptom_quotes
                    if _quote_in_source(quote.strip(), consultation_text)
                )
            )
            if not quotes:
                continue
            seen.add(name_key)
            result.append(candidate.model_copy(update={"supporting_symptom_quotes": quotes}))
        if len(result) < 3:
            raise ValueError(
                "Le modèle local n'a pas produit au moins trois hypothèses correctement citées."
            )
        return result[:7]

    def generate_grounded_differential(
        self,
        consultation_text: str,
        facts: list[PatientEvidence],
        document_inventory: list[dict[str, object]],
        profiles: list[MedicalKnowledgeProfile] | None = None,
        existing_hypotheses: list[str] | None = None,
    ) -> list[QwenGroundedHypothesisCandidate]:
        output = self.generate_structured(
            self.QWEN_GROUNDED_DIFFERENTIAL_PROMPT,
            {
                "consultation": consultation_text,
                "document_inventory": document_inventory,
                "patient_evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "type": item.type,
                        "description": item.description,
                        "date": item.date,
                        "polarity": item.polarity,
                    }
                    for item in facts
                ],
                "medical_knowledge_profiles": [
                    item.model_dump(mode="json") for item in (profiles or [])
                ],
                "existing_hypotheses_to_avoid": existing_hypotheses or [],
            },
            QwenGroundedDifferentialOutput,
            max_tokens=2400,
        )
        result: list[QwenGroundedHypothesisCandidate] = []
        seen: set[str] = set()
        for item in output.hypotheses:
            key = normalize(item.disease_name).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
        if len(result) < 3:
            raise ValueError("Qwen n'a pas produit cinq hypothèses distinctes valides.")
        result = [
            item for item in result
            if item.concept_type in {ClinicalConceptType.DISEASE, ClinicalConceptType.ETIOLOGY}
        ]
        if len(result) < 3:
            raise ValueError("Qwen differential contains fewer than three valid etiologies.")
        return result[:7]

    def review_grounded_hypothesis(
        self,
        hypothesis: QwenGroundedHypothesisCandidate,
        facts: list[PatientEvidence],
        profiles: list[MedicalKnowledgeProfile] | None = None,
    ) -> QwenHypothesisReview:
        output = self.generate_structured(
            self.QWEN_HYPOTHESIS_REVIEW_PROMPT,
            {
                "hypothesis": hypothesis.model_dump(mode="json"),
                "allowed_evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "type": item.type,
                        "description": item.description,
                        "date": item.date,
                        "polarity": item.polarity,
                    }
                    for item in facts
                ],
                "medical_knowledge_profiles": [
                    item.model_dump(mode="json") for item in (profiles or [])
                ],
            },
            QwenHypothesisReview,
            max_tokens=1800,
        )
        if normalize(output.disease_name) != normalize(hypothesis.disease_name):
            raise ValueError("Qwen a modifié le nom de l'hypothèse pendant sa revue.")
        allowed = {item.evidence_id for item in facts}
        def clean(values: list[str]) -> list[str]:
            return list(dict.fromkeys(item for item in values if item in allowed))
        return output.model_copy(
            update={
                "disease_name": hypothesis.disease_name,
                "supporting_evidence_ids": clean(output.supporting_evidence_ids),
                "contradicting_evidence_ids": clean(output.contradicting_evidence_ids),
                "discriminative_evidence_ids": clean(output.discriminative_evidence_ids),
            }
        )

    def compare_grounded_hypotheses(
        self,
        reviews: list[QwenHypothesisReview],
        *,
        attempt: int = 1,
        previous_error: str | None = None,
    ) -> QwenComparativeRanking:
        retry_instruction = (
            "\nLa tentative précédente a été rejetée. Corrige strictement le contrat JSON; "
            f"motif technique: {previous_error[:200]}."
            if attempt > 1 and previous_error
            else ""
        )
        expected_ids = [item.hypothesis_id for item in reviews]
        output = self.generate_structured(
            self.QWEN_COMPARISON_PROMPT + retry_instruction,
            {"hypothesis_reviews": [item.model_dump(mode="json") for item in reviews]},
            QwenComparativeRanking,
            max_tokens=2400,
            seed=41 + attempt,
            json_contract=(
                '{"ordered_hypothesis_ids":['
                + ",".join(json.dumps(item, ensure_ascii=False) for item in expected_ids)
                + '],"ordered_disease_names":[],"assessments":[{'
                '"hypothesis_id":"identifiant fourni",'
                '"diagnostic_status":"DOCUMENTED_CONFIRMED|STRONGLY_SUPPORTED|SUPPORTED|POSSIBLE|CONTRADICTED|INSUFFICIENT_EVIDENCE",'
                '"evidence_strength":"DECISIVE|HIGHLY_DISCRIMINATING|SPECIFIC|NON_SPECIFIC|CONTRADICTORY|INSUFFICIENT",'
                '"supporting_evidence_ids":[],"contradicting_evidence_ids":[],'
                '"discriminative_evidence_ids":[],"missing_information":[],'
                '"alternative_diagnoses":[],"comparative_justification":"justification",'
                '"uncertainty":"LOW_FOR_DOCUMENTED_CONFIRMATION|MODERATE|HIGH|UNDETERMINED",'
                '"reference_ids":[],"relative_position":1,"limitations":[]}],'
                '"can_prioritize_one":false,'
                '"conclusion":"synthèse courte fondée uniquement sur les relations validées"}'
                "\nordered_hypothesis_ids doit contenir chacun des identifiants fournis "
                "exactement une fois, dans l'ordre qualitatif proposé. assessments doit contenir "
                "une entrée par hypothèse."
            ),
        )
        allowed_ids = set(expected_ids)
        ordered_ids = list(dict.fromkeys(output.ordered_hypothesis_ids))
        if not ordered_ids and output.ordered_disease_names:
            by_name = {normalize(item.disease_name): item.hypothesis_id for item in reviews}
            ordered_ids = [
                by_name[normalize(name)]
                for name in output.ordered_disease_names
                if normalize(name) in by_name
            ]
            ordered_ids = list(dict.fromkeys(ordered_ids))
        if set(ordered_ids) != allowed_ids or len(ordered_ids) != len(reviews):
            raise ValueError("Qwen comparison did not include every hypothesis exactly once.")
        if output.assessments:
            assessment_ids = [item.hypothesis_id for item in output.assessments]
            if set(assessment_ids) != allowed_ids or len(assessment_ids) != len(reviews):
                raise ValueError("Qwen assessments did not cover every hypothesis exactly once.")
            allowed_evidence = {
                review.hypothesis_id: {
                    str(relation.get("evidence_id"))
                    for relation in review.validated_relations
                    if relation.get("evidence_id")
                }
                for review in reviews
            }
            cleaned_assessments = []
            for assessment in output.assessments:
                valid_ids = allowed_evidence.get(assessment.hypothesis_id, set())
                cleaned_assessments.append(
                    assessment.model_copy(update={
                        "supporting_evidence_ids": [
                            item for item in assessment.supporting_evidence_ids
                            if item in valid_ids
                        ],
                        "contradicting_evidence_ids": [
                            item for item in assessment.contradicting_evidence_ids
                            if item in valid_ids
                        ],
                        "discriminative_evidence_ids": [
                            item for item in assessment.discriminative_evidence_ids
                            if item in valid_ids
                        ],
                        "reference_ids": [
                            item for item in assessment.reference_ids if item in valid_ids
                        ],
                    })
                )
        else:
            cleaned_assessments = []
        names = {item.hypothesis_id: item.disease_name for item in reviews}
        return output.model_copy(
            update={
                "ordered_hypothesis_ids": ordered_ids,
                "ordered_disease_names": [names[item] for item in ordered_ids],
                "assessments": cleaned_assessments,
            }
        )

    def close(self) -> None:
        with self._lock:
            self._model = None
        gc.collect()
