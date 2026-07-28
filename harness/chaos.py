"""Process-kill primitives and CLI used by the durability chaos harness."""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class KillOutcome:
    command: tuple[str, ...]
    delay_seconds: float
    killed: bool
    returncode: int
    elapsed_seconds: float
    stdout: str
    stderr: str


def delay_schedule(
    runs: int,
    *,
    seed: int,
    minimum: float,
    maximum: float,
) -> list[float]:
    if runs <= 0:
        raise ValueError("runs must be positive")
    if minimum < 0 or maximum < minimum:
        raise ValueError("invalid delay range")
    rng = random.Random(seed)
    return [rng.uniform(minimum, maximum) for _ in range(runs)]


def _popen_group_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # The PID comes directly from Popen, not user input.
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode != 0 and process.poll() is None:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_with_forced_kill(
    command: Sequence[str],
    *,
    cwd: Path,
    delay_seconds: float,
    environment: dict[str, str] | None = None,
) -> KillOutcome:
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must contain non-empty strings")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must not be negative")

    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_popen_group_options(),
    )
    killed = False
    try:
        try:
            process.wait(timeout=delay_seconds)
        except subprocess.TimeoutExpired:
            killed = True
            _kill_process_tree(process)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            _kill_process_tree(process)
            process.wait(timeout=5)

    return KillOutcome(
        command=tuple(command),
        delay_seconds=delay_seconds,
        killed=killed,
        returncode=int(process.returncode),
        elapsed_seconds=time.monotonic() - started,
        stdout=stdout,
        stderr=stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repeatedly kill a target command at seeded random times"
    )
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260728)
    parser.add_argument("--min-delay", type=float, default=0.005)
    parser.add_argument("--max-delay", type=float, default=0.250)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("a target command is required after --", file=sys.stderr)
        return 2

    delays = delay_schedule(
        args.runs,
        seed=args.seed,
        minimum=args.min_delay,
        maximum=args.max_delay,
    )
    for index, delay in enumerate(delays, start=1):
        outcome = run_with_forced_kill(
            command,
            cwd=args.cwd.resolve(),
            delay_seconds=delay,
            environment=os.environ.copy(),
        )
        record = {"iteration": index, **asdict(outcome)}
        print(json.dumps(record, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
