"""Exercise actual Windows CLI paths before and after the portability fix."""

import json
import os
from pathlib import Path
import subprocess
import sys

assert sys.platform == "win32", "This proof requires native Windows paths"
BASELINE = "df7a1399c2e083d0225375620452d1e0fde6fb14"
GUARDS = ("private_test_access.py", "private_test_bindings.py", "private_test_flow.py", "private_test_expressions.py")


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    candidate = os.environ["PROBE_CANDIDATE"]
    assert git("rev-parse", "HEAD") == candidate
    assert os.environ["PROBE_BASELINE"] == BASELINE
    root = Path(os.environ["RUNNER_TEMP"]) / "slf001-windows-paths"
    root.mkdir(parents=True, exist_ok=True)
    cases = {
        "private-target": "getattr(mod, '_helper')\n",
        "syntax-error": "def broken(:\n",
    }
    revisions = {"baseline": BASELINE}
    if os.environ["PROBE_BASELINE_ONLY"] != "true":
        revisions["candidate"] = candidate
    results = {}
    for label, revision in revisions.items():
        folder = root / label
        scripts = folder / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        for name in GUARDS:
            if not git("ls-tree", "--name-only", revision, "--", f"scripts/{name}"):
                continue
            source = subprocess.check_output(
                ["git", "show", f"{revision}:scripts/{name}"], text=True
            )
            (scripts / name).write_text(source, encoding="utf-8")
        sample = folder / "tests" / "src" / "unit" / "test_sample.py"
        sample.parent.mkdir(parents=True, exist_ok=True)
        for name, source in cases.items():
            sample.write_text(source, encoding="utf-8")
            command = [sys.executable, str(scripts / GUARDS[0]), "--repo", str(folder)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            results[f"{label}-{name}"] = {
                "revision": revision,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "posix_prefix": result.stdout.startswith("tests/src/unit/test_sample.py:"),
                "windows_prefix": result.stdout.startswith("tests\\src\\unit\\test_sample.py:"),
            }
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    assert len(results) == 2 * len(revisions)
    for label, result in results.items():
        assert result["returncode"] == 1 and not result["stderr"], (label, result)
        fixed = label.startswith("candidate-")
        assert result["posix_prefix"] is fixed, (label, result)
        assert result["windows_prefix"] is not fixed, (label, result)
    print("Both diagnostic forms reproduced native Windows separator failures and satisfied the expected revision contract.")


if __name__ == "__main__":
    main()
