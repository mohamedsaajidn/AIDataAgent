from functools import lru_cache

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

# Presidio's phone recognizer often scores as low as 0.4 for valid numbers -
# this is a broad recall-oriented screen (not yet an enforcement gate, see
# Phase 7), so a lower bar trades some false positives for catching phone PII.
_SCORE_THRESHOLD = 0.35


@lru_cache
def _get_analyzer() -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])


def scan_column(sample_values: list) -> tuple[bool, list[str]]:
    text = " | ".join(str(v) for v in sample_values if v is not None)
    if not text.strip():
        return False, []

    results = _get_analyzer().analyze(text=text, language="en")
    entities = sorted({r.entity_type for r in results if r.score >= _SCORE_THRESHOLD})
    return bool(entities), entities
