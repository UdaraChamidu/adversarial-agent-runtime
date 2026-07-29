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
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.store import EventStore, RunNotFoundError
from mockllm.server import create_server


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


def run_agent_chaos(
    *,
    runs: int,
    workspace_root: Path,
    seed: int,
    minimum_delay: float = 0.002,
    maximum_delay: float = 0.030,
) -> dict[str, object]:
    """Kill one process per logical email run, resume, and verify exactly once."""

    if runs <= 0:
        raise ValueError("runs must be positive")
    workspace_root.mkdir(parents=True, exist_ok=True)
    batch_id = uuid.uuid4().hex[:10]
    server = create_server(port=0, seed=seed)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    delays = delay_schedule(
        runs, seed=seed, minimum=minimum_delay, maximum=maximum_delay
    )
    passed = 0
    killed_count = 0
    failures: list[dict[str, object]] = []
    try:
        for index, delay in enumerate(delays, start=1):
            workspace = workspace_root / f"{batch_id}-{index:03d}"
            workspace.mkdir(parents=True, exist_ok=False)
            run_id = f"chaos-{batch_id}-{index:03d}"
            task = (
                "Send exactly one email to recipient@example.test "
                "with the approved chaos message."
            )
            run_command = [
                sys.executable,
                "-m",
                "agent",
                "run",
                "--task",
                task,
                "--scenario",
                "S1",
                "--run-id",
                run_id,
                "--workspace",
                str(workspace),
                "--base-url",
                base_url,
            ]
            killed = run_with_forced_kill(
                run_command,
                cwd=Path.cwd(),
                delay_seconds=delay,
                environment=os.environ.copy(),
            )
            killed_count += int(killed.killed)

            store = EventStore(workspace / "agent.db")
            store.initialize()
            try:
                store.load_events(run_id)
                finish_command = [
                    sys.executable,
                    "-m",
                    "agent",
                    "resume",
                    run_id,
                    "--workspace",
                    str(workspace),
                    "--base-url",
                    base_url,
                ]
            except RunNotFoundError:
                finish_command = run_command
            finished = subprocess.run(
                finish_command,
                cwd=Path.cwd(),
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            emails = store.list_emails(run_id)
            state = store.rebuild_state(run_id)
            try:
                store.verify_event_chain(run_id)
                chain_ok = True
            except Exception:
                chain_ok = False
            if (
                finished.returncode == 0
                and state.status == "completed"
                and len(emails) == 1
                and chain_ok
            ):
                passed += 1
            else:
                failures.append(
                    {
                        "iteration": index,
                        "killed": killed.killed,
                        "finish_returncode": finished.returncode,
                        "email_count": len(emails),
                        "status": state.status,
                        "chain_ok": chain_ok,
                        "stdout": finished.stdout,
                        "stderr": finished.stderr,
                    }
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return {
        "runs": runs,
        "passed": passed,
        "failed": runs - passed,
        "kills_observed": killed_count,
        "seed": seed,
        "batch_id": batch_id,
        "failures": failures,
    }


def main(argv: Sequence[str] | None = None) -> int:
    selected_argv = list(argv) if argv is not None else sys.argv[1:]
    if selected_argv and selected_argv[0] == "agent":
        parser = argparse.ArgumentParser(
            description="Run end-to-end kill/resume email chaos trials"
        )
        parser.add_argument("mode", choices=["agent"])
        parser.add_argument("--runs", type=int, default=100)
        parser.add_argument("--seed", type=int, default=20260728)
        parser.add_argument(
            "--workspace-root", type=Path, default=Path("workspace") / "chaos"
        )
        args = parser.parse_args(selected_argv)
        report = run_agent_chaos(
            runs=args.runs,
            workspace_root=args.workspace_root.resolve(),
            seed=args.seed,
        )
        print(json.dumps(report, sort_keys=True), flush=True)
        return 0 if report["failed"] == 0 and report["kills_observed"] > 0 else 1

    args = build_parser().parse_args(selected_argv)
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
