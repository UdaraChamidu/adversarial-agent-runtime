"""Command-line contract for the Part A runtime."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="start a durable agent run")
    run_parser.add_argument("--task", required=True)

    resume_parser = subparsers.add_parser(
        "resume", help="resume a previously interrupted run"
    )
    resume_parser.add_argument("run_id")

    replay_parser = subparsers.add_parser(
        "replay", help="replay recorded decisions without the model server"
    )
    replay_parser.add_argument("run_id")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    parser.error(
        f"{args.command!r} is part of the public contract but is not implemented yet"
    )
    return 2
