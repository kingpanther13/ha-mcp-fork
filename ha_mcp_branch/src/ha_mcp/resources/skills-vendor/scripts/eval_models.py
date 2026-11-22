#!/usr/bin/env python3
"""Print the model IDs that `claude plugin eval` runs actually used.

The results JSON records only aliases: `sonnet` or `haiku` per case, the
--model override and the --judge-model. An alias moves to a newer model on a
release, so two results files with the same alias can come from different
models. The resolved ID is written only in each run's transcript, as the
`model` of every assistant message, and transcripts exist only for runs made
with --keep-temp. The judge's resolved model is not recorded anywhere.

For each results file, prints every case and arm with the model IDs its runs
used and how many runs used each. A run without a readable transcript is
listed as such, and the script exits 1, so a comparison is not made on a
model nobody checked.

Usage: python scripts/eval_models.py <results.json> [...]
"""
import json
import sys
from collections import Counter


def trace_models(path):
    """Return the set of assistant-message model IDs in one transcript."""
    models = set()
    with open(path, encoding="utf-8") as trace:
        for line in trace:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # a killed run leaves a cut-off last line
            if event.get("type") == "assistant":
                model = (event.get("message") or {}).get("model")
                if model:
                    models.add(model)
    return models


def report(results_path):
    """Print one results file's models; return the number of runs without one."""
    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)
    suite = results.get("suite", {})
    print(f"{results_path}: --model {suite.get('modelOverride') or '(case pin)'}, "
          f"judge {suite.get('judgeModel') or '(default)'}")
    unknown = 0
    for case in results.get("cases", []):
        for arm, runs in case.get("arms", {}).items():
            counts = Counter()
            for run in runs:
                path = run.get("tracePath")
                try:
                    models = trace_models(path) if path else set()
                except OSError:
                    models = set()
                counts[", ".join(sorted(models)) or "no transcript"] += 1
            unknown += counts["no transcript"]
            found = "; ".join(f"{model} x{n}" for model, n in sorted(counts.items()))
            print(f"  {case['name']} [{arm}] (pin {case.get('model')}): {found}")
    return unknown


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    unknown = sum(report(path) for path in sys.argv[1:])
    if unknown:
        print(f"{unknown} run(s) without a transcript: rerun with --keep-temp, "
              "or read the model before the temp directory is removed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
