"""Standalone chart-testing script.

Edit QUESTION (and DATASET_ID if you want a different dataset) below, then:
    uv run python scripts/test_charts.py

Or override without editing the file:
    uv run python scripts/test_charts.py <dataset_id> "<question>"

Calls the Q&A agent directly - no API server needed, just the usual
`docker compose up` infra (Postgres/MinIO) and the sandbox image built. If
the response includes a chart, it's saved to a real .html file. This
sidesteps the JSON-escaping trap of copying `chart_html` out of a raw HTTP
response by hand (a literal "\\n" in the copied text stays "\\n" instead of
becoming a real newline, and the browser shows garbage) - here there's no
HTTP/JSON layer at all, just a plain Python string.

Don't just double-click the saved file - open the printed http://localhost
URL instead. The chart's plotly.js <script> tag uses SRI (integrity +
crossorigin), which silently fails to execute under a bare file:// page in
most browsers, producing a blank page with no visible error. The first run
starts a small local server (in the background, port 8899 by default) that
keeps serving chart_output/ across every future run - after that, just
refresh the same browser tab each time you re-run this script.
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

# The model's output sometimes contains characters Windows' default console
# codepage (cp1252) can't encode (e.g. a non-breaking hyphen) - without this,
# a plain print() of such a response crashes this script outright instead of
# just displaying it. reconfigure() is available on Python 3.7+ text streams.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent.graphs.qa import ask  # noqa: E402 - must follow the stdout/stderr reconfigure above

# --- Edit these and re-run ---
DATASET_ID = "82d3c767-bde4-4852-8113-ed42aaac04f6"
QUESTION = "Show me an bar chart of count of products launched every year"
# ------------------------------

CHART_DIR = Path(__file__).parent / "chart_output"
OUTPUT_PATH = CHART_DIR / "chart.html"
SERVER_PORT = 8899


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _ensure_chart_server() -> str:
    """Starts a detached local HTTP server over CHART_DIR if one isn't
    already running on SERVER_PORT, then returns the chart's URL.

    Opening the saved .html file directly (file://) shows a blank page: the
    chart's plotly.js <script> tag uses SRI (integrity + crossorigin), and
    that check silently fails under a file:// origin in most browsers, so
    plotly.js never actually runs. Serving over http://localhost fixes it.
    The server is started detached (new process group, no window, own
    stdout/stderr) so it outlives this script and keeps serving across every
    future run - only the very first run needs to start it.
    """
    url = f"http://localhost:{SERVER_PORT}/chart.html"
    if _port_is_open(SERVER_PORT):
        return url

    CHART_DIR.mkdir(exist_ok=True)
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            [sys.executable, "-m", "http.server", str(SERVER_PORT), "--directory", str(CHART_DIR)],
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)  # give it a moment to bind before anyone opens the URL
    except Exception as exc:
        print(f"(couldn't start a local server automatically: {exc} - open {OUTPUT_PATH} manually instead)")
    return url


def main() -> None:
    dataset_id = sys.argv[1] if len(sys.argv) > 1 else DATASET_ID
    question = sys.argv[2] if len(sys.argv) > 2 else QUESTION

    print(f"dataset_id: {dataset_id}")
    print(f"question:   {question}")
    print()

    result = ask(dataset_id, question)

    print(f"route:      {result.route}")
    print(f"attempts:   {result.attempts}")
    print(f"latency:    {result.duration_seconds:.2f}s")
    print(f"tokens:     {result.prompt_tokens} prompt / {result.completion_tokens} completion")
    print(f"answer:     {result.answer}")
    print(f"result:     {result.result}")
    if result.error:
        print(f"error:      {result.error}")
    if result.code:
        print()
        print("--- generated code ---")
        print(result.code)

    print()
    if result.chart_html:
        CHART_DIR.mkdir(exist_ok=True)
        OUTPUT_PATH.write_text(result.chart_html, encoding="utf-8")
        url = _ensure_chart_server()
        print(f"Chart saved to: {OUTPUT_PATH}")
        print(f"Open in your browser: {url}")
        print("(the server keeps running in the background - just refresh that tab on future runs)")
    else:
        print("No chart was produced for this question/route.")


if __name__ == "__main__":
    main()
