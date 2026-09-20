"""Runs inside the sandbox container only - this is the trusted half of the
execution boundary. The untrusted half is whatever it reads from
/scratch/user_code.py, which the host writes before starting the container.
"""

import json
import traceback
from pathlib import Path

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
    namespace = {"datasets": datasets, "pd": pd, "ARTIFACTS_DIR": str(ARTIFACTS_DIR)}

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
