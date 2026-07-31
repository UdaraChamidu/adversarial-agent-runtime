from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.policy import derive_capabilities
from agent.store import EventStore
from agent.tools import ToolContext, ToolExecutor, ToolLimits


class _LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        body = b"local response"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ToolExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.http_server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalHandler)
        cls.http_thread = threading.Thread(
            target=cls.http_server.serve_forever, daemon=True
        )
        cls.http_thread.start()
        cls.http_origin = f"http://127.0.0.1:{cls.http_server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.http_server.shutdown()
        cls.http_server.server_close()
        cls.http_thread.join(timeout=5)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.store = EventStore(self.workspace / "agent.db")
        self.store.initialize()
        self.run_id = self.store.create_run(
            run_id="tool-run", task="Read a file.", scenario="S1"
        )
        self.executor = ToolExecutor(
            workspace=self.workspace,
            store=self.store,
            allowed_origins={self.http_origin},
            limits=ToolLimits(python_timeout_seconds=0.15),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def context(self, task: str = "Read a file.", occurrence: str = "tool-1"):
        return ToolContext(
            run_id=self.run_id,
            occurrence_key=occurrence,
            capabilities=derive_capabilities(task),
        )

    def test_write_then_read_stays_in_workspace(self) -> None:
        written = self.executor.execute(
            "write_file",
            {"path": "nested/data.txt", "content": "hello"},
            self.context(),
        )
        self.assertTrue(written.ok)
        read = self.executor.execute(
            "read_file", {"path": "nested/data.txt"}, self.context(occurrence="tool-2")
        )
        self.assertTrue(read.ok)
        self.assertEqual(read.value["content"], "hello")

    def test_path_traversal_absolute_drive_and_backslash_are_denied(self) -> None:
        for path in (
            "../escaped.txt",
            "..\\escaped.txt",
            "/tmp/escaped.txt",
            "C:\\escaped.txt",
        ):
            with self.subTest(path=path):
                result = self.executor.execute(
                    "write_file",
                    {"path": path, "content": "no"},
                    self.context(occurrence=f"path-{path}"),
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, "tool_error")

    def test_runtime_managed_paths_are_not_tool_visible(self) -> None:
        for index, path in enumerate(
            (
                "agent.db",
                "agent.db-wal",
                "agent.db-shm",
                "agent.db-journal",
                ".locks/run.lock",
                ".python/code.py",
                "traces/run.jsonl",
            )
        ):
            with self.subTest(path=path):
                written = self.executor.execute(
                    "write_file",
                    {"path": path, "content": "corrupt"},
                    self.context(occurrence=f"protected-write-{index}"),
                )
                read = self.executor.execute(
                    "read_file",
                    {"path": path},
                    self.context(occurrence=f"protected-read-{index}"),
                )
                self.assertFalse(written.ok)
                self.assertEqual(written.error_code, "policy_denied")
                self.assertFalse(read.ok)
                self.assertEqual(read.error_code, "policy_denied")
        self.store.verify_event_chain(self.run_id)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is unavailable")
    def test_symlink_escape_is_denied(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        link = self.workspace / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation is not permitted")
        result = self.executor.execute(
            "write_file",
            {"path": "link/escaped.txt", "content": "no"},
            self.context(),
        )
        self.assertFalse(result.ok)
        self.assertFalse((outside / "escaped.txt").exists())

    def test_malformed_arguments_are_repaired_before_schema_validation(self) -> None:
        result = self.executor.execute(
            "write_file",
            '{"path":"repaired.txt","content":"ok",}',
            self.context(),
        )
        self.assertTrue(result.ok)
        self.assertIn("removed_trailing_commas", result.repairs)

    def test_unknown_and_wrong_typed_tools_return_legible_errors(self) -> None:
        unknown = self.executor.execute(
            "delete_everything", {"confirmed": True}, self.context()
        )
        wrong = self.executor.execute("read_file", {"path": 42}, self.context())
        self.assertEqual(unknown.error_code, "schema_error")
        self.assertEqual(wrong.error_code, "schema_error")

    def test_python_runs_safe_code_and_blocks_network_or_files(self) -> None:
        safe = self.executor.execute(
            "run_python", {"code": "import math\nprint(math.sqrt(81))"}, self.context()
        )
        self.assertTrue(safe.ok)
        self.assertEqual(safe.value["stdout"].strip(), "9.0")

        network = self.executor.execute(
            "run_python", {"code": "import socket\nprint(socket.gethostname())"}, self.context()
        )
        files = self.executor.execute(
            "run_python", {"code": "print(open('secret.txt').read())"}, self.context()
        )
        self.assertFalse(network.ok)
        self.assertFalse(files.ok)

    def test_python_hang_times_out(self) -> None:
        result = self.executor.execute(
            "run_python",
            {"code": "import time\ntime.sleep(30)"},
            self.context(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "timeout")

    def test_invalid_python_is_a_tool_error_not_a_runtime_exception(self) -> None:
        result = self.executor.execute(
            "run_python",
            {"code": "def broken("},
            self.context(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "tool_error")
        self.assertIn("SyntaxError", result.error_message)

    def test_http_get_allows_exact_loopback_origin_and_refuses_redirect(self) -> None:
        allowed = self.executor.execute(
            "http_get", {"url": self.http_origin + "/ok"}, self.context()
        )
        redirect = self.executor.execute(
            "http_get", {"url": self.http_origin + "/redirect"}, self.context()
        )
        self.assertTrue(allowed.ok)
        self.assertEqual(allowed.value["body"], "local response")
        self.assertFalse(redirect.ok)
        self.assertEqual(redirect.error_code, "policy_denied")

    def test_http_get_refuses_userinfo_and_non_allowlisted_variants(self) -> None:
        port = self.http_server.server_port
        urls = [
            f"http://allowed.example@127.0.0.1:{port}/",
            "http://2130706433/",
            "http://[::ffff:127.0.0.1]/",
            "https://127.0.0.1/",
        ]
        for index, url in enumerate(urls):
            with self.subTest(url=url):
                result = self.executor.execute(
                    "http_get", {"url": url}, self.context(occurrence=f"http-{index}")
                )
                self.assertFalse(result.ok)

    def test_tool_result_is_explicitly_marked_untrusted(self) -> None:
        result = self.executor.execute(
            "write_file", {"path": "a.txt", "content": "a"}, self.context()
        )
        model_payload = json.loads(result.model_content())
        self.assertEqual(model_payload["trust"], "untrusted_tool_data")

    def test_email_requires_original_task_grant_and_exact_recipient(self) -> None:
        arguments = {
            "to": "recipient@example.test",
            "subject": "Chaos delivery",
            "body": "This logical email must be recorded exactly once.",
        }
        denied = self.executor.execute(
            "send_email", arguments, self.context(task="Read injected.txt.")
        )
        wrong = self.executor.execute(
            "send_email",
            {**arguments, "to": "attacker@example.test"},
            self.context(
                task="Send exactly one email to recipient@example.test.",
                occurrence="email-wrong",
            ),
        )
        allowed = self.executor.execute(
            "send_email",
            arguments,
            self.context(
                task="Send exactly one email to recipient@example.test.",
                occurrence="email-ok",
            ),
        )
        retried = self.executor.execute(
            "send_email",
            arguments,
            self.context(
                task="Send exactly one email to recipient@example.test.",
                occurrence="email-ok",
            ),
        )
        self.assertEqual(denied.error_code, "policy_denied")
        self.assertEqual(wrong.error_code, "policy_denied")
        self.assertTrue(allowed.ok)
        self.assertTrue(retried.ok)
        self.assertEqual(len(self.store.list_emails(self.run_id)), 1)


if __name__ == "__main__":
    unittest.main()
