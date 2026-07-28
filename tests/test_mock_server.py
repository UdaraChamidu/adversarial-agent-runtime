from __future__ import annotations

import http.client
import json
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from typing import Any

from mockllm.protocol import CONTEXT_LIMIT, MODEL_NAME
from mockllm.scenarios import load_scenarios
from mockllm.server import create_server


def request_payload(
    *,
    text: str = "Exercise the selected scenario.",
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "model": MODEL_NAME,
        "max_tokens": 512,
        "messages": [{"role": "user", "content": text}],
        "tools": [],
        "metadata": {"request_id": request_id or uuid.uuid4().hex},
    }


class MockServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.server.reset_attempts()

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        scenario: str | None,
        request_id: str | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        headers = {"Content-Type": "application/json"}
        if scenario is not None:
            headers["X-Scenario-ID"] = scenario
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                body = json.loads(response.read())
                return response.status, body, dict(response.headers)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
            return exc.code, body, dict(exc.headers)

    def test_health_lists_all_scenarios(self) -> None:
        with urllib.request.urlopen(self.base_url + "/health", timeout=3) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["scenarios"], [f"S{number}" for number in range(1, 13)])

    def test_catalog_is_complete_and_data_driven(self) -> None:
        scenarios = load_scenarios()
        self.assertEqual(set(scenarios), {f"S{number}" for number in range(1, 13)})
        self.assertEqual(scenarios["S8"].params["critical_fact"], "ORCHID-73")

    def test_s1_returns_messages_shaped_tool_call(self) -> None:
        status, body, _ = self._post(
            "/v1/messages", request_payload(), scenario="S1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["type"], "message")
        self.assertEqual(body["role"], "assistant")
        self.assertEqual(body["stop_reason"], "tool_use")
        self.assertEqual(body["content"][0]["name"], "read_file")
        self.assertGreater(body["usage"]["input_tokens"], 0)

    def test_s2_keeps_transport_json_valid_but_arguments_malformed(self) -> None:
        status, body, _ = self._post(
            "/v1/messages", request_payload(), scenario="S2"
        )
        self.assertEqual(status, 200)
        inputs = [block["input"] for block in body["content"]]
        self.assertTrue(all(isinstance(value, str) for value in inputs))
        for value in inputs:
            with self.assertRaises(json.JSONDecodeError):
                json.loads(value)

    def test_s5_interrupts_once_then_returns_same_logical_message(self) -> None:
        request_id = uuid.uuid4().hex
        payload = request_payload(request_id=request_id)
        request = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Scenario-ID": "S5",
                "X-Request-ID": request_id,
            },
            method="POST",
        )
        with self.assertRaises((http.client.IncompleteRead, ConnectionError)):
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()

        status, body, _ = self._post(
            "/v1/messages", payload, scenario="S5", request_id=request_id
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["content"][0]["name"], "read_file")

    def test_s6_returns_429_then_529_then_200(self) -> None:
        request_id = uuid.uuid4().hex
        payload = request_payload(request_id=request_id)
        first = self._post(
            "/v1/messages", payload, scenario="S6", request_id=request_id
        )
        second = self._post(
            "/v1/messages", payload, scenario="S6", request_id=request_id
        )
        third = self._post(
            "/v1/messages", payload, scenario="S6", request_id=request_id
        )
        self.assertEqual([first[0], second[0], third[0]], [429, 529, 200])
        self.assertEqual(first[2]["Retry-After"], "0")

    def test_s12_partial_turn_is_not_valid_json_then_retry_is_complete(self) -> None:
        request_id = uuid.uuid4().hex
        payload = request_payload(request_id=request_id)
        request = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Scenario-ID": "S12",
                "X-Request-ID": request_id,
            },
            method="POST",
        )
        with self.assertRaises((http.client.IncompleteRead, ConnectionError)):
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()

        status, body, _ = self._post(
            "/v1/messages", payload, scenario="S12", request_id=request_id
        )
        self.assertEqual(status, 200)
        calls = [block for block in body["content"] if block["type"] == "tool_use"]
        self.assertEqual([call["id"] for call in calls], ["partial-1", "partial-2", "partial-3"])

    def test_context_limit_is_enforced_by_server(self) -> None:
        status, body, _ = self._post(
            "/v1/messages",
            request_payload(text="abcdefgh " * (CONTEXT_LIMIT + 1)),
            scenario="S1",
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["type"], "context_length_exceeded")

    def test_unknown_scenario_is_legible(self) -> None:
        status, body, _ = self._post(
            "/v1/messages", request_payload(), scenario="S404"
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["type"], "unknown_scenario")


if __name__ == "__main__":
    unittest.main()
