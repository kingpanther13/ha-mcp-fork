"""Throwaway: take one test file off every list of exempt tests."""

import pathlib
import sys

target = sys.argv[1].strip()
path = pathlib.PurePosixPath(target)
tree = "tests/addon" if target.startswith("tests/addon/") else "tests/src/unit"
relative = path.relative_to(tree).as_posix()
module = target.removesuffix(".py").replace("/", ".")

edits = [(rule, f'  - "{target}"\n') for rule in pathlib.Path("rules").glob("*.yml")]
edits.append((pathlib.Path(tree, "ruff.toml"), f'"{relative}" = ["SLF001"]\n'))
edits.append((pathlib.Path("tests/core_stand_in_guard.py"), f'        "{target}",\n'))
edits.append((pathlib.Path("pyproject.toml"), f'    "{module}",\n'))

for file, line in edits:
    text = file.read_text(encoding="utf-8")
    if line in text:
        file.write_text(text.replace(line, ""), encoding="utf-8", newline="\n")
        print(f"DELISTED from {file.as_posix()}")
