from functools import lru_cache

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

# Presidio's phone recognizer often scores as low as 0.4 for valid numbers -
# this is a broad recall-oriented screen (not yet an enforcement gate, see
# Phase 7), so a lower bar trades some false positives for catching phone PII.
_SCORE_THRESHOLD = 0.35

# NER-based entity types (spaCy, not a pattern/checksum recognizer) are
# unreliable on short, decontextualized catalog values with no sentence
# structure to disambiguate - a brand/product/category name routinely scores
# as high-confidence PERSON or ORGANIZATION to a small generic NER model
# (observed: "Kaya Resolvist" -> PERSON at 0.85, "Conditioner" -> PERSON at
# 0.85). Only suppressed on columns whose name is clearly about a business/
# catalog entity, not an actual individual.
_NER_ENTITIES = {"PERSON", "ORGANIZATION", "LOCATION"}
_BUSINESS_COLUMN_KEYWORDS = (
    "product", "brand", "company", "category", "market", "region",
    "country", "department", "sku", "vendor", "supplier", "manufacturer",
)
# DATE_TIME's regex recognizer matches plain numbers that happen to look
# date-shaped (observed: an 8-digit numeric ID -> DATE_TIME at 0.85). A bare
# "this looks like a date" hit is a weak, frequently-wrong privacy signal
# regardless of column - dates are already captured separately by the
# profiler's own "datetime" type inference, not by PII scanning.
_ALWAYS_EXCLUDED_ENTITIES = {"DATE_TIME"}


@lru_cache
def _get_analyzer() -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])


def scan_column(column_name: str, sample_values: list) -> tuple[bool, list[str]]:
    text = " | ".join(str(v) for v in sample_values if v is not None)
    if not text.strip():
        return False, []

    is_business_column = any(kw in column_name.lower() for kw in _BUSINESS_COLUMN_KEYWORDS)

    entities: set[str] = set()
    for r in _get_analyzer().analyze(text=text, language="en"):
        if r.score < _SCORE_THRESHOLD or r.entity_type in _ALWAYS_EXCLUDED_ENTITIES:
            continue
        if r.entity_type in _NER_ENTITIES and is_business_column:
            continue
        entities.add(r.entity_type)

    return bool(entities), sorted(entities)
