"""Throwaway: collect every test module that imports from the given files.

A converted file that stops exporting a name breaks the modules that import
it. This collects those modules with pytest so an ImportError shows up.
"""

import pathlib
import re
import subprocess
import sys

targets = [pathlib.PurePosixPath(arg) for arg in sys.argv[1:]]
stems = {target.stem for target in targets}
pattern = re.compile(
    r"^\s*from\s+[.\w]*\b(" + "|".join(map(re.escape, stems)) + r")\s+import\b"
    r"|^\s*from\s+[.\w]+\s+import\s+[^#\n]*\b(" + "|".join(map(re.escape, stems)) + r")\b",
    re.MULTILINE,
)
importers = sorted(
    path.as_posix()
    for tree in ("tests/src/unit", "tests/addon")
    for path in pathlib.Path(tree).rglob("*.py")
    if path.as_posix() not in {str(t) for t in targets}
    and pattern.search(path.read_text(encoding="utf-8"))
)
if not importers:
    print("no other module imports from these files")
    sys.exit(0)
print("modules importing from these files:", *importers, sep="\n  ")
proc = subprocess.run(
    [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *importers],
    capture_output=True,
    text=True,
    check=False,
)
tail = (proc.stdout + proc.stderr).strip().splitlines()[-40:]
print("\n".join(tail))
sys.exit(proc.returncode)
