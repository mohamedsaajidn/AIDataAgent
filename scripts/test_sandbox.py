"""Manual verification suite for the Phase 2 sandbox executor.

Run everything:      uv run python scripts/test_sandbox.py
Run one scenario:     uv run python scripts/test_sandbox.py network
Run a few:            uv run python scripts/test_sandbox.py network timeout memory
"""

import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from sandbox.executor import run_python

_PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _tmp_parquet(df: pd.DataFrame) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="sandbox-test-"))
    path = tmp_dir / "data.parquet"
    df.to_parquet(path, index=False)
    return path


def test_happy_path() -> tuple[bool, str]:
    path = _tmp_parquet(pd.DataFrame({"x": range(10), "y": [v * 2 for v in range(10)]}))
    code = 'df = datasets["data"]\nresult = {"row_count": len(df), "sum_y": int(df["y"].sum())}'
    res = run_python(code, {"data": path}, timeout_seconds=20)
    ok = res.error is None and res.result == {"row_count": 10, "sum_y": 90}
    return ok, f"result={res.result} error={res.error}"


def test_network_blocked() -> tuple[bool, str]:
    code = (
        "import urllib.request\n"
        'resp = urllib.request.urlopen("http://example.com", timeout=5)\n'
        "result = resp.status\n"
    )
    res = run_python(code, {}, timeout_seconds=15)
    ok = res.error is not None and ("resolution" in res.error.lower() or "network" in res.error.lower())
    return ok, f"error={res.error!r}"


def test_filesystem_isolation() -> tuple[bool, str]:
    code = """
attempts = {}
target = "/" + "etc" + "/" + "passwd"
try:
    with open(target, "a") as f:
        f.write("pwned")
    attempts["write_etc_passwd"] = "SUCCEEDED (BAD)"
except Exception:
    attempts["write_etc_passwd"] = "blocked"

data_path = "/" + "data" + "/" + "x.txt"
try:
    with open(data_path, "w") as f:
        f.write("no")
    attempts["write_data_mount"] = "SUCCEEDED (BAD)"
except Exception:
    attempts["write_data_mount"] = "blocked"

scratch_path = "/" + "scratch" + "/" + "ok.txt"
try:
    with open(scratch_path, "w") as f:
        f.write("yes")
    attempts["write_scratch"] = "succeeded"
except Exception:
    attempts["write_scratch"] = "blocked (unexpected)"

result = attempts
"""
    res = run_python(code, {}, timeout_seconds=15)
    r = res.result or {}
    ok = (
        r.get("write_etc_passwd") == "blocked"
        and r.get("write_data_mount") == "blocked"
        and r.get("write_scratch") == "succeeded"
    )
    return ok, f"result={res.result} error={res.error}"


def test_timeout() -> tuple[bool, str]:
    code = "while True:\n    pass\n"
    t0 = time.monotonic()
    res = run_python(code, {}, timeout_seconds=8)
    elapsed = time.monotonic() - t0
    ok = res.timed_out and elapsed < 15
    return ok, f"timed_out={res.timed_out} elapsed={elapsed:.1f}s"


def test_memory_bomb() -> tuple[bool, str]:
    code = "x = [0] * (10**10)\nresult = len(x)\n"
    t0 = time.monotonic()
    res = run_python(code, {}, timeout_seconds=20)
    elapsed = time.monotonic() - t0
    ok = res.oom_killed and elapsed < 10
    return ok, f"oom_killed={res.oom_killed} elapsed={elapsed:.1f}s error={res.error}"


def test_concurrency() -> tuple[bool, str]:
    path_a = _tmp_parquet(pd.DataFrame({"secret": ["A-only"]}))
    path_b = _tmp_parquet(pd.DataFrame({"secret": ["B-only"]}))

    code_a = (
        'import time\ntime.sleep(2)\n'
        'result = {"visible": list(datasets.keys()), "value": datasets["a"]["secret"].iloc[0]}'
    )
    code_b = (
        'import time\ntime.sleep(2)\n'
        'result = {"visible": list(datasets.keys()), "value": datasets["b"]["secret"].iloc[0]}'
    )

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_a = ex.submit(run_python, code_a, {"a": path_a}, 15)
        fut_b = ex.submit(run_python, code_b, {"b": path_b}, 15)
        res_a, res_b = fut_a.result(), fut_b.result()

    ok = res_a.result == {"visible": ["a"], "value": "A-only"} and res_b.result == {
        "visible": ["b"],
        "value": "B-only",
    }
    return ok, f"A={res_a.result} B={res_b.result}"


def test_chart_artifact() -> tuple[bool, str]:
    code = """
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([1, 2, 3], [4, 5, 6])
fig.savefig(f"{ARTIFACTS_DIR}/chart.png")
result = "saved"
"""
    res = run_python(code, {}, timeout_seconds=20)
    ok = "chart.png" in res.artifacts and res.artifacts["chart.png"][:8] == _PNG_MAGIC
    return ok, f"artifacts={list(res.artifacts.keys())} error={res.error}"


def test_blocklist() -> tuple[bool, str]:
    code = "import subprocess\nsubprocess.run(['ls'])\n"
    res = run_python(code, {}, timeout_seconds=10)
    ok = res.rejected
    return ok, f"rejected={res.rejected} error={res.error}"


def test_cold_start_latency() -> tuple[bool, str]:
    times = []
    for _ in range(3):
        t0 = time.monotonic()
        run_python("result = 1 + 1", {}, timeout_seconds=15)
        times.append(time.monotonic() - t0)
    avg = sum(times) / len(times)
    ok = avg < 5.0
    return ok, f"runs={[round(t, 2) for t in times]} avg={avg:.2f}s"


TESTS = {
    "happy_path": test_happy_path,
    "network": test_network_blocked,
    "filesystem": test_filesystem_isolation,
    "timeout": test_timeout,
    "memory": test_memory_bomb,
    "concurrency": test_concurrency,
    "chart": test_chart_artifact,
    "blocklist": test_blocklist,
    "latency": test_cold_start_latency,
}


def main() -> None:
    names = sys.argv[1:] or list(TESTS.keys())
    print(f"Running {len(names)} sandbox test(s)...\n")

    all_ok = True
    for name in names:
        if name not in TESTS:
            print(f"[{name}] unknown test - choices are: {', '.join(TESTS)}")
            all_ok = False
            continue
        try:
            ok, details = TESTS[name]()
        except Exception as exc:
            ok, details = False, f"raised {exc!r}"
        all_ok = all_ok and ok
        print(f"[{name}] {'PASS' if ok else 'FAIL'} - {details}\n")

    print("=== ALL PASSED ===" if all_ok else "=== SOME FAILED ===")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
