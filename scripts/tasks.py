"""Cross-platform implementation behind the required Make targets."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def setup() -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.", file=sys.stderr)
        return 1
    installed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--editable",
            str(ROOT),
        ],
        check=False,
    )
    if installed.returncode != 0:
        return installed.returncode
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    commands = {name: shutil.which(name) for name in ("agent", "mockllm")}
    missing = [name for name, path in commands.items() if path is None]
    if missing:
        print(
            "Installed console commands are not on PATH in this shell: "
            + ", ".join(missing)
            + ". Use the documented 'python -m' form or add Python's Scripts "
            "directory to PATH."
        )
    for module in ("agent", "mockllm"):
        checked = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if checked.returncode != 0:
            print(f"Installed module command failed: {module}", file=sys.stderr)
            return checked.returncode or 1
    print(f"Python: {sys.version.split()[0]}")
    print(f"SQLite: {sqlite3.sqlite_version}")
    print(f"Workspace: {WORKSPACE}")
    print(f"Agent command: {commands['agent'] or f'{sys.executable} -m agent'}")
    print(f"Mock command: {commands['mockllm'] or f'{sys.executable} -m mockllm'}")
    return 0


def test() -> int:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def eval_command() -> int:
    from evals.runner import main as eval_main

    return eval_main([])


def chaos() -> int:
    from harness.chaos import run_agent_chaos

    runs = int(os.environ.get("CHAOS_RUNS", "100"))
    report = run_agent_chaos(
        runs=runs,
        workspace_root=WORKSPACE / "chaos",
        seed=20260728,
    )
    print(__import__("json").dumps(report, sort_keys=True))
    return 0 if report["failed"] == 0 and report["kills_observed"] > 0 else 1


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
