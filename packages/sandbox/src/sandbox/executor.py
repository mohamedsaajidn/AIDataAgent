import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker

from sandbox.blocklist import check_code

IMAGE_NAME = "dataagent-sandbox:latest"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_PIDS_LIMIT = 64
DEFAULT_NANO_CPUS = 1_000_000_000  # 1 CPU


@dataclass
class ExecutionResult:
    stdout: str = ""
    stderr: str = ""
    result: object = None
    error: str | None = None
    artifacts: dict[str, bytes] = field(default_factory=dict)
    duration_seconds: float = 0.0
    timed_out: bool = False
    rejected: bool = False
    oom_killed: bool = False


def run_python(
    code: str,
    datasets: dict[str, Path],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ExecutionResult:
    """Execute `code` in an isolated, ephemeral container.

    `datasets` maps a name (used as the parquet filename, without extension)
    to a local file path readable on the host - the caller is responsible for
    already having pulled these from wherever they're actually stored.
    """
    violations = check_code(code)
    if violations:
        return ExecutionResult(error="Code rejected by static safety check: " + "; ".join(violations), rejected=True)

    work_dir = Path(tempfile.mkdtemp(prefix="sandbox-run-"))
    data_dir = work_dir / "data"
    scratch_dir = work_dir / "scratch"
    artifacts_dir = scratch_dir / "artifacts"
    data_dir.mkdir()
    scratch_dir.mkdir()
    artifacts_dir.mkdir()

    try:
        for name, path in datasets.items():
            shutil.copy(path, data_dir / f"{name}.parquet")
        (scratch_dir / "user_code.py").write_text(code, encoding="utf-8")

        stdout, stderr, timed_out, oom_killed, duration = _run_container(data_dir, scratch_dir, timeout_seconds)
        result_value, error_value = _read_output(scratch_dir, stderr, timed_out, oom_killed, timeout_seconds)
        artifacts = {f.name: f.read_bytes() for f in artifacts_dir.glob("*") if f.is_file()}

        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            result=result_value,
            error=error_value,
            artifacts=artifacts,
            duration_seconds=duration,
            timed_out=timed_out,
            oom_killed=oom_killed,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_container(data_dir: Path, scratch_dir: Path, timeout_seconds: int) -> tuple[str, str, bool, bool, float]:
    client = docker.from_env(timeout=max(timeout_seconds * 2, 60))
    container = None
    start = time.monotonic()
    timed_out = False
    oom_killed = False
    try:
        container = client.containers.run(
            IMAGE_NAME,
            detach=True,
            network_mode="none",
            mem_limit=DEFAULT_MEMORY_LIMIT,
            memswap_limit=DEFAULT_MEMORY_LIMIT,
            nano_cpus=DEFAULT_NANO_CPUS,
            pids_limit=DEFAULT_PIDS_LIMIT,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            read_only=True,
            tmpfs={"/tmp": "size=100m"},
            user="sandbox",
            volumes={
                str(data_dir.resolve()): {"bind": "/data", "mode": "ro"},
                str(scratch_dir.resolve()): {"bind": "/scratch", "mode": "rw"},
            },
        )
        try:
            container.wait(timeout=timeout_seconds)
            container.reload()
            oom_killed = bool(container.attrs.get("State", {}).get("OOMKilled"))
        except Exception:
            timed_out = True
            try:
                container.kill()
            except Exception:
                pass

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass

    return stdout, stderr, timed_out, oom_killed, time.monotonic() - start


def _read_output(
    scratch_dir: Path, stderr: str, timed_out: bool, oom_killed: bool, timeout_seconds: int
) -> tuple[object, str | None]:
    if timed_out:
        return None, f"Execution timed out after {timeout_seconds}s"
    if oom_killed:
        return None, "Execution was killed for exceeding the memory limit"

    output_path = scratch_dir / "output.json"
    if not output_path.exists():
        return None, stderr.strip() or "Sandbox produced no output (it may have crashed or been killed)"

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, "Sandbox produced unreadable output"

    return payload.get("result"), payload.get("error")
