from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import unittest
from pathlib import Path

import agent
import mockllm
from agent.cli import build_parser as build_agent_parser
from mockllm.server import build_parser as build_mock_parser


ROOT = Path(__file__).resolve().parents[1]


class ScaffoldTests(unittest.TestCase):
    def test_supported_python_version(self) -> None:
        self.assertGreaterEqual(sys.version_info, (3, 11))

    def test_packages_expose_versions(self) -> None:
        self.assertEqual(agent.__version__, "0.1.0")
        self.assertEqual(mockllm.__version__, "0.1.0")

    def test_agent_cli_contract_parses(self) -> None:
        args = build_agent_parser().parse_args(["run", "--task", "read a file"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.task, "read a file")

    def test_mock_cli_contract_parses(self) -> None:
        args = build_mock_parser().parse_args(["--port", "9000"])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 9000)

    def test_setup_installs_public_console_commands(self) -> None:
        scripts = {
            entry.name: entry.value
            for entry in importlib.metadata.entry_points(group="console_scripts")
        }
        self.assertEqual(scripts.get("agent"), "agent.cli:main")
        self.assertEqual(scripts.get("mockllm"), "mockllm.server:main")
        for name in ("agent", "mockllm"):
            with self.subTest(command=name):
                completed = subprocess.run(
                    [sys.executable, "-m", name, "--help"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_runtime_workspace_exists(self) -> None:
        self.assertTrue((ROOT / "workspace").is_dir())


if __name__ == "__main__":
    unittest.main()
