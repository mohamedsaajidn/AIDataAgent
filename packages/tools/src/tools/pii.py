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


def scan_column(column_name: str, sample_values: list, is_categorical: bool = False) -> tuple[bool, list[str]]:
    text = " | ".join(str(v) for v in sample_values if v is not None)
    if not text.strip():
        return False, []

    is_business_column = any(kw in column_name.lower() for kw in _BUSINESS_COLUMN_KEYWORDS)
    # The keyword list above only catches the false-positive class when the
    # *column name* hints at it. It doesn't when the name doesn't (observed:
    # "DelphiFormatName" -> "Conditioner"/"Shampoo" values scored PERSON at
    # 0.85, same as the "Conditioner" case the keyword list was built for,
    # but "format" isn't a business keyword). A low-cardinality categorical
    # column is a controlled vocabulary being repeated across rows, not free
    # text about an individual, regardless of what its name happens to be -
    # the caller passes the profiler's own inferred_type so this doesn't
    # depend on guessing every possible naming convention up front.
    suppress_ner = is_business_column or is_categorical

    entities: set[str] = set()
    for r in _get_analyzer().analyze(text=text, language="en"):
        if r.score < _SCORE_THRESHOLD or r.entity_type in _ALWAYS_EXCLUDED_ENTITIES:
            continue
        if r.entity_type in _NER_ENTITIES and suppress_ner:
            continue
        entities.add(r.entity_type)

    return bool(entities), sorted(entities)


_REDACTED = "[redacted: PII column]"


def redact_pii_from_result(result: object, pii_columns: set[str], pii_values: set[str] | None = None) -> object:
    """Enforcement, not a hint: strips PII-flagged data out of an
    already-executed result, before it can become the user-facing answer or
    get replayed into a future prompt via short-term memory
    (agent.memory.append_turn stores exactly this `answer` text verbatim).
    scan_column/is_pii only ever fed a *warning* into the code-gen prompt
    ("don't echo raw values from this column") - the model could ignore it,
    or write code that selects the column without realizing it. This checks
    what the code actually returned.

    Two independent checks, because a PII leak doesn't always carry the
    column name with it:
    - By column NAME: any value keyed by a PII-flagged column name (a plain
      dict/list-of-dicts from .to_dict(), or the sandbox's dataframe-preview
      shape {"type": "dataframe", "columns": [...], "preview": [...]} - the
      latter needs its "columns" list filtered too, not just each preview
      record, so a caller doesn't even see the PII column was selected).
    - By VALUE: `pii_values` (the PII columns' actual values, read from the
      real dataset by the caller - see agent.graphs.qa._node_execute) - a
      bare leaf scalar/string matching one exactly gets redacted even with
      no column name anywhere in the structure. This is load-bearing, not
      redundant with the name-based check: `df["region"].unique().tolist()`
      returns a plain list of strings with no column name attached at all -
      verified live, this was a real leak the name-only check missed
      entirely (a PII-flagged "region" column's raw values reached the
      final answer untouched).

    Known gap, not covered here: a PII column's raw *values* used as dict
    keys (e.g. `df.groupby("email")[...].to_dict()` produces
    {"a@x.com": 5, ...}). Redacting a key would either leave the leaked
    value in place as a key anyway, or collapse multiple distinct keys onto
    the same placeholder and silently lose data - neither is a real fix, so
    this is left as a known gap rather than a false sense of coverage.
    """
    if not pii_columns:
        return result

    lowered = {c.lower() for c in pii_columns}
    values = {str(v) for v in pii_values} if pii_values else set()

    def _is_pii_value(value: object) -> bool:
        return bool(values) and isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value) in values

    def _redact(value: object) -> object:
        if isinstance(value, dict):
            if value.get("type") == "dataframe" and "columns" in value:
                redacted = dict(value)
                redacted["columns"] = [c for c in value["columns"] if c.lower() not in lowered]
                redacted["preview"] = [_redact(row) for row in value.get("preview", [])]
                return redacted
            return {
                k: (_REDACTED if (isinstance(k, str) and k.lower() in lowered) else _redact(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_REDACTED if _is_pii_value(item) else _redact(item) for item in value]
        if _is_pii_value(value):
            return _REDACTED
        return value

    return _redact(result)
