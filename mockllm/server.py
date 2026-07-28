"""Mock server entry point.

The wire contract is implemented in the next infrastructure milestone.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mockllm")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise SystemExit(
        f"mockllm contract scaffold exists at {args.host}:{args.port}; "
        "the server is not implemented yet"
    )
