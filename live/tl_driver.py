"""Drive translate_locales against the stub with the real clock.

usage: tl_driver.py <script> <scenario> <port>
Prints one JSON line: elapsed, failures, results, error.
"""

import importlib.util
import json
import os
import sys
import time

script, scenario, port = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["GEMINI_API_KEY"] = "stub"
os.environ["GEMINI_API_URL"] = f"http://127.0.0.1:{port}/"
spec = importlib.util.spec_from_file_location("translate_locales", script)
tl = importlib.util.module_from_spec(spec)
sys.modules["translate_locales"] = tl
spec.loader.exec_module(tl)

out: dict = {}
start = time.monotonic()
if scenario == "call":  # one _call_gemini against an always-503 engine
    tl._DEADLINE = start + 3600
    try:
        tl._call_gemini("prompt")
    except SystemExit as exc:
        out["error"] = str(exc)
else:
    budget = {"surface": 3600, "late": 1}[scenario.split("-")[0]]
    tl._DEADLINE = start + budget
    tl._MAX_CHARS_PER_REQUEST = 1  # one string per chunk
    items = [
        tl.WorkItem("de", "messages", "live_test_a", "Save settings"),
        tl.WorkItem("de", "messages", "live_test_b", "Restart server"),
        tl.WorkItem("de", "messages", "live_test_c", "Open logs"),
    ]
    by_id = {tl._string_id(i): i for i in items}
    batch = {tl._string_id(i): i.english for i in items}
    results: dict = {}
    failures: list = []
    failed: set = set()
    try:
        dead, count = tl._translate_surface(
            "de", "messages", batch, by_id, set(), results, failures, failed, 0
        )
        out.update(dead=dead, count=count)
    except SystemExit as exc:
        out["error"] = str(exc)
    out.update(results={"|".join(k): v for k, v in results.items()}, failures=failures)
out["elapsed"] = round(time.monotonic() - start, 1)
print(json.dumps(out))
