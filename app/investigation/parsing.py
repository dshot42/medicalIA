from __future__ import annotations

import re
import unicodedata

from app.models.investigation import ParsedConsultation, SymptomObservation, SymptomStatus

SYMPTOM_PATTERNS: dict[str, tuple[str, ...]] = {
    "douleurs abdominales": ("douleur abdominal", "douleurs abdominal", "mal au ventre"),
    "nausées": ("nausee", "nausees"),
    "vomissements": ("vomissement", "vomissements"),
    "fatigue": ("fatigue", "asthenie"),
    "faiblesse musculaire": ("faiblesse musculaire", "diminution de la force", "paresie"),
    "constipation": ("constipation",),
    "diarrhée": ("diarrhee",),
    "ballonnements": ("ballonnement",),
    "perte de poids": ("perte de poids", "amaigrissement"),
    "fièvre": ("fievre",),
    "ictère": ("ictere", "jaunisse"),
    "urines foncées": ("urines foncees", "urines rouges", "urines brunes"),
    "vertiges": ("vertige", "etourdissement"),
}


def normalize(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(char)
    )


def _is_denied(text: str, start: int) -> bool:
    prefix = text[max(0, start - 45) : start]
    return bool(
        re.search(
            r"(?:pas de|sans|aucun(?:e)?|nie|ne rapporte pas de|absence de)\s+(?:\w+\s+){0,3}$",
            prefix,
        )
    )


def parse_consultation_text(text: str) -> ParsedConsultation:
    normalized = normalize(text)
    observations: list[SymptomObservation] = []
    for canonical, variants in SYMPTOM_PATTERNS.items():
        matches = [normalized.find(variant) for variant in variants if variant in normalized]
        if not matches:
            continue
        start = min(position for position in matches if position >= 0)
        status = SymptomStatus.DENIED if _is_denied(normalized, start) else SymptomStatus.PRESENT
        temporal = None
        if any(term in normalized for term in ("intermittent", "par episode", "par crise")):
            temporal = "intermittent"
        elif any(term in normalized for term in ("persistant", "continu")):
            temporal = "persistant"
        severity = None
        if any(term in normalized for term in ("intense", "severe", "importante")):
            severity = "importante ou sévère selon le texte rapporté"
        observations.append(
            SymptomObservation(
                name=canonical,
                status=status,
                temporal_pattern=temporal,
                severity=severity,
                source_text=text,
            )
        )

    duration_match = re.search(
        r"depuis\s+(plusieurs\s+)?(\d+\s+)?(jour|semaine|mois|an(?:nee)?s?)\w*",
        normalized,
    )
    duration = duration_match.group(0) if duration_match else None
    severity = (
        "importante ou sévère selon le texte rapporté"
        if any(term in normalized for term in ("intense", "severe", "importante"))
        else None
    )
    missing: list[str] = []
    if duration is None:
        missing.append("durée globale des symptômes")
    if not re.search(r"dure(?:e|nt)?\s+\d+|pendant\s+\d+", normalized):
        missing.append("durée de chaque épisode")
    if not any(term in normalized for term in ("declench", "apres", "lie a", "favoris")):
        missing.append("facteurs déclenchants")
    if not any(term in normalized for term in ("selle", "transit", "repas")):
        missing.append("relation avec les repas et le transit")
    if not any(term in normalized for term in ("medicament", "traitement", "prise de")):
        missing.append("médicaments et expositions récents")
    return ParsedConsultation(
        symptoms=observations,
        duration=duration,
        severity=severity,
        missing_details=missing,
        not_documented=missing,
    )


def expanded_search_terms(parsed: ParsedConsultation) -> list[str]:
    expansions = {
        "douleurs abdominales": ["douleur abdominale", "mal au ventre"],
        "faiblesse musculaire": ["faiblesse musculaire", "diminution de la force", "parésie"],
        "fatigue": ["fatigue", "asthénie"],
        "nausées": ["nausées", "vomissements"],
    }
    terms: list[str] = []
    for symptom in parsed.symptoms:
        if symptom.status != SymptomStatus.PRESENT:
            continue
        terms.extend(expansions.get(symptom.name, [symptom.name]))
    return list(dict.fromkeys(terms))

