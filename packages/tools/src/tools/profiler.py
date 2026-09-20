import re

import pandas as pd

_ID_NAME_PATTERN = re.compile(r"(^id$|_id$|^uuid$|^guid$)", re.IGNORECASE)
# Short, code-shaped values (SKUs, phone-like codes, UUIDs) - excludes emails
# (has "@"/".") and free text (has spaces) so those fall through to "text" instead.
_ID_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_ID_MIN_RATIO = 0.98
_ID_MIN_DISTINCT = 50
_CATEGORICAL_MAX_DISTINCT = 20
_CATEGORICAL_MAX_RATIO = 0.05
_TEXT_MIN_AVG_LEN = 30
_DATETIME_PARSE_SUCCESS_THRESHOLD = 0.9
_BOOLEAN_TOKENS = {"true", "false", "yes", "no", "y", "n", "0", "1"}

ALLOWED_TYPES = {"id", "categorical", "continuous", "datetime", "boolean", "text"}


def is_plausible_override(inferred_type: str, distinct_count: int) -> bool:
    """Reject an LLM type override that contradicts the column's own cardinality."""
    if inferred_type == "categorical" and distinct_count > _CATEGORICAL_MAX_DISTINCT:
        return False
    if inferred_type == "boolean" and distinct_count > 2:
        return False
    return True


def profile_dataframe(df: pd.DataFrame) -> list[dict]:
    return [_profile_column(df[col], position=i) for i, col in enumerate(df.columns)]


def _profile_column(series: pd.Series, position: int) -> dict:
    n = len(series)
    non_null = series.dropna()
    null_count = n - len(non_null)
    distinct_count = int(non_null.nunique())
    cardinality_ratio = distinct_count / len(non_null) if len(non_null) else 0.0

    inferred_type, ambiguous = _infer_type(series.name, series, non_null, distinct_count, cardinality_ratio)

    return {
        "position": position,
        "name": str(series.name),
        "pandas_dtype": str(series.dtype),
        "inferred_type": inferred_type,
        "ambiguous": ambiguous,
        "null_count": int(null_count),
        "null_pct": float(null_count / n) if n else 0.0,
        "distinct_count": distinct_count,
        "cardinality_ratio": float(cardinality_ratio),
        "sample_values": _sample_values(non_null),
    }


def _infer_type(
    name: object, series: pd.Series, non_null: pd.Series, distinct_count: int, cardinality_ratio: float
) -> tuple[str, bool]:
    if len(non_null) == 0:
        return "text", False

    if _ID_NAME_PATTERN.search(str(name)):
        return "id", False

    is_numeric = pd.api.types.is_numeric_dtype(series)
    looks_high_cardinality = cardinality_ratio >= _ID_MIN_RATIO and distinct_count > _ID_MIN_DISTINCT

    # Restricted to integers, not all numerics: a continuous float measurement
    # (a price, an amount) is naturally ~100% unique without being an
    # identifier - only integer surrogate/sequential keys fit that pattern.
    if looks_high_cardinality and pd.api.types.is_integer_dtype(series):
        return "id", False

    if pd.api.types.is_datetime64_any_dtype(series) or (
        pd.api.types.is_object_dtype(series) and _looks_like_datetime(non_null)
    ):
        return "datetime", False

    lowered = set(non_null.astype(str).str.strip().str.lower().unique())
    if distinct_count <= 2 and lowered.issubset(_BOOLEAN_TOKENS):
        return "boolean", False

    if looks_high_cardinality and not is_numeric:
        sample_str = non_null.astype(str).head(50)
        id_shaped = sample_str.str.fullmatch(_ID_VALUE_PATTERN).mean() >= 0.9
        if id_shaped:
            # Structurally code-like, but the name alone can't tell a phone
            # number from a SKU - let the LLM disambiguate using the column name.
            return "id", True

    # A small-integer numeric column is ambiguous: it could be a rating
    # (categorical) or a count (continuous) - flag it for the LLM to confirm.
    if is_numeric and 3 <= distinct_count <= _CATEGORICAL_MAX_DISTINCT:
        return "categorical", True

    if distinct_count <= _CATEGORICAL_MAX_DISTINCT or cardinality_ratio < _CATEGORICAL_MAX_RATIO:
        return "categorical", False

    if is_numeric:
        return "continuous", False

    avg_len = non_null.astype(str).str.len().mean()
    if avg_len is not None and avg_len > _TEXT_MIN_AVG_LEN:
        return "text", False

    ambiguous = 0.3 <= cardinality_ratio < 0.6
    return ("categorical" if cardinality_ratio < 0.5 else "text"), ambiguous


def _looks_like_datetime(non_null: pd.Series) -> bool:
    sample = non_null.sample(min(50, len(non_null)), random_state=0)
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    return parsed.notna().mean() >= _DATETIME_PARSE_SUCCESS_THRESHOLD


def _sample_values(non_null: pd.Series, k: int = 5) -> list:
    uniques = non_null.drop_duplicates().head(k)
    return [_to_jsonable(v) for v in uniques.tolist()]


def _to_jsonable(value: object) -> object:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)
