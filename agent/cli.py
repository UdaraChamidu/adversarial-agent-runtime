"""Command-line interface for durable run, resume, and replay operations."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from agent.model_client import MessagesClient
from agent.runtime import AgentRuntime
from agent.replay import replay_run
from agent.store import EventStore, RunNotFoundError


def _runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.environ.get("AGENT_WORKSPACE", "workspace")),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MOCKLLM_BASE_URL", "http://127.0.0.1:8765"),
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="exact localhost origin available to http_get; repeatable",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="start a durable agent run")
    run_parser.add_argument("--task", required=True)
    run_parser.add_argument("--scenario", default="S1", choices=[f"S{i}" for i in range(1, 13)])
    run_parser.add_argument("--run-id")
    _runtime_options(run_parser)

    resume_parser = subparsers.add_parser(
        "resume", help="resume a previously interrupted run"
    )
    resume_parser.add_argument("run_id")
    _runtime_options(resume_parser)

    replay_parser = subparsers.add_parser(
        "replay", help="replay recorded decisions without the model server"
    )
    replay_parser.add_argument("run_id")
    replay_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.environ.get("AGENT_WORKSPACE", "workspace")),
    )

    return parser


def _build_runtime(args: argparse.Namespace) -> tuple[EventStore, AgentRuntime]:
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    store = EventStore(workspace / "agent.db")
    store.initialize()
    runtime = AgentRuntime(
        workspace=workspace,
        store=store,
        client=MessagesClient(args.base_url),
        allowed_origins=set(args.allow_origin),
    )
    return store, runtime


def _print_outcome(outcome) -> int:
    print(
        json.dumps(
            {
                "run_id": outcome.run_id,
                "status": outcome.status,
                "final_text": outcome.final_text,
                "reason": outcome.reason,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if outcome.status == "failed" else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "replay":
        workspace = args.workspace.resolve()
        store = EventStore(workspace / "agent.db")
        store.initialize()
        try:
            report = replay_run(store, args.run_id)
        except RunNotFoundError:
            parser.error(f"run {args.run_id!r} does not exist")
        print(
            json.dumps(
                {
                    "run_id": report.run_id,
                    "matches_recording": report.matches_recording,
                    "decision_count": report.decision_count,
                    "decision_hash": report.decision_hash,
                    "terminal_status": report.terminal_status,
                    "errors": list(report.errors),
                },
                sort_keys=True,
            )
        )
        return 0 if report.matches_recording else 1
    _store, runtime = _build_runtime(args)
    try:
        if args.command == "run":
            return _print_outcome(
                runtime.start(
                    task=args.task, scenario=args.scenario, run_id=args.run_id
                )
            )
        return _print_outcome(runtime.resume(args.run_id))
    except RunNotFoundError:
        parser.error(f"run {args.run_id!r} does not exist")
    return 2
