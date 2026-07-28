"""Cross-platform implementation behind the required Make targets."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"


def setup() -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.", file=sys.stderr)
        return 1
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print(f"Python: {sys.version.split()[0]}")
    print(f"SQLite: {sqlite3.sqlite_version}")
    print(f"Workspace: {WORKSPACE}")
    return 0


def test() -> int:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def eval_command() -> int:
    print("The eval runner will be added in milestone M7.", file=sys.stderr)
    return 2


def chaos() -> int:
    print("The chaos harness will be added before Part A durability work.", file=sys.stderr)
    return 2


def clean() -> int:
    resolved_workspace = WORKSPACE.resolve()
    resolved_root = ROOT.resolve()
    if resolved_workspace.parent != resolved_root:
        print("Refusing to clean an unexpected workspace path.", file=sys.stderr)
        return 1
    for child in resolved_workspace.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    print(f"Cleaned generated state under {resolved_workspace}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("setup", "test", "eval", "chaos", "clean"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    command = build_parser().parse_args(argv).command
    commands = {
        "setup": setup,
        "test": test,
        "eval": eval_command,
        "chaos": chaos,
        "clean": clean,
    }
    return commands[command]()


if __name__ == "__main__":
    raise SystemExit(main())
