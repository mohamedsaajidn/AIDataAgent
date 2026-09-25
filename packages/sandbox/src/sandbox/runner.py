"""Runs inside the sandbox container only - this is the trusted half of the
execution boundary. The untrusted half is whatever it reads from
/scratch/user_code.py, which the host writes before starting the container.
"""

import json
import os
import traceback
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

DATA_DIR = Path("/data")
SCRATCH_DIR = Path("/scratch")
ARTIFACTS_DIR = SCRATCH_DIR / "artifacts"
USER_CODE_PATH = SCRATCH_DIR / "user_code.py"
OUTPUT_PATH = SCRATCH_DIR / "output.json"


def _load_datasets() -> dict[str, pd.DataFrame]:
    datasets = {}
    if DATA_DIR.exists():
        for path in sorted(DATA_DIR.glob("*.parquet")):
            datasets[path.stem] = pd.read_parquet(path)
    return datasets


def _serialize(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        return {
            "type": "dataframe",
            "columns": [str(c) for c in value.columns],
            "row_count": len(value),
            "preview": json.loads(value.head(100).to_json(orient="records")),
        }
    if isinstance(value, pd.Series):
        return {"type": "series", "data": json.loads(value.to_json())}
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def main() -> None:
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    datasets = _load_datasets()
    # Generated code has repeatedly (observed live, multiple times) reached
    # for os.getenv("ARTIFACTS_DIR") instead of the bare namespace variable
    # below - that always returned None and either silently skipped saving
    # the artifact or wrote it to a guessed fallback path the executor never
    # looks at, with no Python error either way. Setting the real env var
    # too means either style resolves correctly - this doesn't change how
    # the namespace variable already works, it just makes the other common
    # way of looking this up also correct instead of prompting around it.
    os.environ["ARTIFACTS_DIR"] = str(ARTIFACTS_DIR)
    # numpy is already a sandbox image dependency (scikit-learn/scipy/plotly
    # all pull it in) but wasn't pre-imported like pandas - observed live: a
    # regression/stats question wrote np.polyfit(...) without its own import
    # and got a plain NameError, an easily-avoided wasted retry for one of
    # the most common numeric-analysis imports, same rationale as pd below.
    # duckdb is also a sandbox image dependency, same story as np below -
    # observed live (GPT-4o-mini): 3/3 attempts wrote duckdb.query(...) with
    # no import at all, despite the prompt saying "via `import duckdb`" -
    # apparently read as "it's available, reference it as duckdb" by this
    # model rather than "you must import it yourself". Pre-importing it
    # removes the ambiguity instead of relying on prompt wording to survive
    # different models' interpretations.
    namespace = {"datasets": datasets, "pd": pd, "np": np, "duckdb": duckdb, "ARTIFACTS_DIR": str(ARTIFACTS_DIR)}

    error = None
    result_value = None
    try:
        source = USER_CODE_PATH.read_text(encoding="utf-8")
        exec(compile(source, "<user_code>", "exec"), namespace)  # noqa: S102 - this is the sandbox
        result_value = _serialize(namespace.get("result"))
    except Exception:
        error = traceback.format_exc()

    OUTPUT_PATH.write_text(json.dumps({"result": result_value, "error": error}, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
