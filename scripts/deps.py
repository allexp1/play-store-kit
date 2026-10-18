"""Dependency check for the command-line entry points.

Every script calls `deps.require()` before `main()`. When PyYAML or cryptography is missing it prints the exact
install command and exits 1, instead of a traceback on the first `import yaml`.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NEEDED = {"yaml": "PyYAML", "cryptography": "cryptography"}


def missing(modules=tuple(NEEDED)) -> list[str]:
    return [NEEDED.get(m, m) for m in modules if importlib.util.find_spec(m) is None]


def install_hint() -> str:
    venv = ROOT / ".venv"
    req = ROOT / "requirements.txt"
    q = shlex.quote
    return (f"python3 -m venv {q(str(venv))} && {q(str(venv / 'bin' / 'pip'))} install -r {q(str(req))}\n"
            f"then run the script with {q(str(venv / 'bin' / 'python'))}")


def require(modules=tuple(NEEDED)):
    gone = missing(modules)
    if gone:
        print(f"play-store-kit needs {', '.join(gone)} (not installed for {sys.executable}). Install once:\n"
              + install_hint(), file=sys.stderr)
        sys.exit(1)
