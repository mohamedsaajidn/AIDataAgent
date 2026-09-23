import json
import re
from concurrent.futures import ThreadPoolExecutor, wait

from agent.llm import get_chat_model, get_response_text

_ALLOWED_TYPES = {"id", "categorical", "continuous", "datetime", "boolean", "text"}
# A single prompt covering every column doesn't scale: a wide dataset (e.g. 200
# columns) makes the completion large enough that a free-tier model can take
# many minutes or never finish. Batching bounds each call's size regardless of
# how wide the dataset is, and one bad/slow batch doesn't block the others.
_BATCH_SIZE = 20
# Free-tier OpenRouter models are shared/queued - per-call latency is highly
# variable (a single column can take anywhere from ~2s to 30s) independent of
# batch size, so running batches sequentially makes wide datasets scale
# linearly with column count. A small thread pool bounds total wait time
# instead, without risking free-tier rate limits from higher concurrency.
_MAX_CONCURRENT_BATCHES = 4
# Hard ceiling on how long the whole review step may take, independent of how
# many columns/batches there are. A dataset wider than this budget allows
# simply gets partial coverage - the rest keep their heuristic type and no
# description - rather than upload latency scaling with column count times
# however slow the free-tier model happens to be that day.
_TOTAL_TIME_BUDGET_SECONDS = 120

_PROMPT = """You are a data analyst assistant helping build a data dictionary.

For each column below you are given its name, a heuristic-guessed type, whether \
that guess is uncertain, and a few sample values.

Return a JSON array with one object per column, using exactly these keys:
- "name": the column name, copied exactly as given
- "inferred_type": one of "id", "categorical", "continuous", "datetime", "boolean", "text" \
- keep the heuristic guess unless you are confident it is wrong, especially when "ambiguous" is false
- "description": one short plain-English sentence describing what this column likely represents

Columns:
{columns_json}

Respond with ONLY the JSON array, no other text.
"""


def review_columns(profiles: list[dict]) -> dict[str, dict]:
    model = get_chat_model(temperature=0.0)
    batches = [profiles[start : start + _BATCH_SIZE] for start in range(0, len(profiles), _BATCH_SIZE)]

    executor = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_BATCHES)
    futures = [executor.submit(_review_batch, model, batch) for batch in batches]
    done, pending = wait(futures, timeout=_TOTAL_TIME_BUDGET_SECONDS)
    # Don't block returning to the caller on stragglers past the budget - let
    # them finish (or time out) on their own; their results are just unused.
    executor.shutdown(wait=False, cancel_futures=True)
    del pending

    result: dict[str, dict] = {}
    for future in done:
        try:
            result.update(future.result())
        except Exception:
            continue
    return result


def _review_batch(model, batch: list[dict]) -> dict[str, dict]:
    payload = [
        {
            "name": p["name"],
            "heuristic_type": p["inferred_type"],
            "ambiguous": p.get("ambiguous", False),
            "sample_values": p["sample_values"][:5],
        }
        for p in batch
    ]
    response = model.invoke(_PROMPT.format(columns_json=json.dumps(payload)))
    return _parse_response(get_response_text(response))


def _parse_response(text: str) -> dict[str, dict]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return {}
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}

    result: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        inferred_type = item.get("inferred_type")
        result[name] = {
            "inferred_type": inferred_type if inferred_type in _ALLOWED_TYPES else None,
            "description": item.get("description"),
        }
    return result
