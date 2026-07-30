"""Structurally constrained implementations of the five runtime tools."""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agent.arguments import ArgumentError, parse_arguments
from agent.policy import Capabilities, PolicyDeniedError, authorize_email
from agent.schemas import SchemaError, tool_definitions, validate_arguments
from agent.store import EventStore, IdempotencyConflictError


PYTHON_NETWORK_ISOLATION = "ast_policy_only"


@dataclass(frozen=True)
class ToolLimits:
    read_bytes: int = 64 * 1024
    http_bytes: int = 64 * 1024
    python_output_bytes: int = 32 * 1024
    python_timeout_seconds: float = 1.0
    python_memory_bytes: int = 128 * 1024 * 1024


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    value: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    repairs: tuple[str, ...] = ()

    def model_content(self) -> str:
        payload = {
            "trust": "untrusted_tool_data",
            "ok": self.ok,
            "value": self.value,
            "error": (
                None
                if self.ok
                else {"code": self.error_code, "message": self.error_message}
            ),
            "argument_repairs": list(self.repairs),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    occurrence_key: str
    capabilities: Capabilities


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PythonValidator(ast.NodeVisitor):
    allowed_modules = {
        "collections",
        "datetime",
        "decimal",
        "fractions",
        "functools",
        "itertools",
        "json",
        "math",
        "random",
        "re",
        "statistics",
        "string",
        "time",
    }
    forbidden_names = {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root not in self.allowed_modules:
                raise ValueError(f"Python import {root!r} is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if node.level or root not in self.allowed_modules:
            raise ValueError(f"Python import {node.module!r} is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.forbidden_names or node.id.startswith("__"):
            raise ValueError(f"Python name {node.id!r} is not allowed")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise ValueError(f"Python attribute {node.attr!r} is not allowed")
        self.generic_visit(node)


def _unix_process_limits(memory_bytes: int):
    if os.name == "nt":
        return None

    def apply() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1_048_576, 1_048_576))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))

    return apply


class ToolExecutor:
    def __init__(
        self,
        *,
        workspace: Path,
        store: EventStore,
        allowed_origins: set[str] | None = None,
        limits: ToolLimits | None = None,
    ):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.allowed_origins = frozenset(
            self._normalize_origin(origin) for origin in (allowed_origins or set())
        )
        self.limits = limits or ToolLimits()
        self._opener = urllib.request.build_opener(_NoRedirect)

    @staticmethod
    def definitions() -> list[dict[str, Any]]:
        return tool_definitions()

    def execute(
        self,
        tool_name: str,
        raw_arguments: Any,
        context: ToolContext,
    ) -> ToolResult:
        try:
            parsed = parse_arguments(raw_arguments)
            arguments = validate_arguments(tool_name, parsed.value)
            handler = getattr(self, f"_execute_{tool_name}")
            value = handler(arguments, context)
            return ToolResult(ok=True, value=value, repairs=parsed.repairs)
        except ArgumentError as exc:
            return ToolResult(False, error_code="invalid_json", error_message=str(exc))
        except SchemaError as exc:
            return ToolResult(False, error_code="schema_error", error_message=str(exc))
        except PolicyDeniedError as exc:
            return ToolResult(False, error_code="policy_denied", error_message=str(exc))
        except IdempotencyConflictError as exc:
            return ToolResult(
                False, error_code="idempotency_conflict", error_message=str(exc)
            )
        except TimeoutError as exc:
            return ToolResult(False, error_code="timeout", error_message=str(exc))
        except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
            return ToolResult(
                False,
                error_code="tool_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            return ToolResult(
                False,
                error_code="internal_tool_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def _safe_path(self, raw: str) -> Path:
        if "\x00" in raw:
            raise ValueError("path contains a NUL byte")
        normalized = raw.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(raw)
        if posix.is_absolute() or windows.is_absolute() or windows.drive:
            raise ValueError("absolute or drive-qualified paths are not allowed")
        if ".." in posix.parts:
            raise ValueError("parent traversal is not allowed")
        clean_parts = [part for part in posix.parts if part not in {"", "."}]
        if not clean_parts:
            raise ValueError("path must name a file")
        candidate = self.workspace.joinpath(*clean_parts)

        current = self.workspace
        for part in clean_parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError("symlink path components are not allowed")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("path escapes the workspace") from exc
        return resolved

    def _execute_read_file(
        self, arguments: dict[str, str], context: ToolContext
    ) -> dict[str, Any]:
        del context
        path = self._safe_path(arguments["path"])
        if not path.is_file():
            raise FileNotFoundError(f"workspace file does not exist: {arguments['path']}")
        with path.open("rb") as file:
            content = file.read(self.limits.read_bytes + 1)
        truncated = len(content) > self.limits.read_bytes
        visible = content[: self.limits.read_bytes]
        return {
            "path": arguments["path"],
            "content": visible.decode("utf-8", errors="replace"),
            "truncated": truncated,
            "visible_sha256": hashlib.sha256(visible).hexdigest(),
        }

    def _execute_write_file(
        self, arguments: dict[str, str], context: ToolContext
    ) -> dict[str, Any]:
        del context
        path = self._safe_path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after creating parents to reduce symlink-swap exposure.
        path = self._safe_path(arguments["path"])
        encoded = arguments["content"].encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".agent-write-", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {
            "path": arguments["path"],
            "bytes_written": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def _execute_run_python(
        self, arguments: dict[str, str], context: ToolContext
    ) -> dict[str, Any]:
        code = arguments["code"]
        tree = ast.parse(code, mode="exec")
        _PythonValidator().visit(tree)
        sandbox = self.workspace / ".python" / hashlib.sha256(
            context.occurrence_key.encode()
        ).hexdigest()[:16]
        sandbox.mkdir(parents=True, exist_ok=True)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        environment.update(
            {"PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", code],
                cwd=sandbox,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=self.limits.python_timeout_seconds,
                check=False,
                preexec_fn=_unix_process_limits(self.limits.python_memory_bytes),
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Python exceeded {self.limits.python_timeout_seconds:.3f}s"
            ) from exc
        stdout = completed.stdout[: self.limits.python_output_bytes]
        stderr = completed.stderr[: self.limits.python_output_bytes]
        return {
            "returncode": completed.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "output_truncated": (
                len(completed.stdout) > len(stdout) or len(completed.stderr) > len(stderr)
            ),
        }

    @staticmethod
    def _normalize_origin(origin: str) -> str:
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"invalid allowed origin {origin!r}")
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise ValueError(f"allowed origin must not contain credentials or a path: {origin!r}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname.lower()
        rendered_host = f"[{host}]" if ":" in host else host
        return f"{parsed.scheme}://{rendered_host}:{port}"

    def _validate_url(self, raw: str) -> str:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must use http or https and include a host")
        if parsed.username or parsed.password:
            raise ValueError("URL credentials are not allowed")
        origin = self._normalize_origin(
            f"{parsed.scheme}://"
            f"{'[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname}"
            f":{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
        )
        if origin not in self.allowed_origins:
            raise PolicyDeniedError(f"http_get origin is not allow-listed: {origin}")
        host = parsed.hostname.lower()
        if host != "localhost":
            try:
                address = ipaddress.ip_address(host)
            except ValueError as exc:
                raise PolicyDeniedError("only literal loopback addresses or localhost are allowed") from exc
            if not address.is_loopback:
                raise PolicyDeniedError("only loopback destinations are allowed")
        for result in socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(result[4][0]).is_loopback:
                raise PolicyDeniedError("hostname resolved outside loopback")
        return urllib.parse.urlunsplit(parsed)

    def _execute_http_get(
        self, arguments: dict[str, str], context: ToolContext
    ) -> dict[str, Any]:
        del context
        url = self._validate_url(arguments["url"])
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "adversarial-agent-runtime/0.1"}
        )
        try:
            with self._opener.open(request, timeout=3) as response:
                content = response.read(self.limits.http_bytes + 1)
                status = response.status
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise PolicyDeniedError("HTTP redirects are refused") from exc
            raise ValueError(f"HTTP request failed with status {exc.code}") from exc
        truncated = len(content) > self.limits.http_bytes
        return {
            "url": url,
            "status": status,
            "content_type": content_type,
            "body": content[: self.limits.http_bytes].decode("utf-8", errors="replace"),
            "truncated": truncated,
        }

    def _execute_send_email(
        self, arguments: dict[str, str], context: ToolContext
    ) -> dict[str, Any]:
        capability = authorize_email(context.capabilities, arguments["to"])
        effect = self.store.commit_email(
            run_id=context.run_id,
            occurrence_key=context.occurrence_key,
            logical_key=capability.logical_key,
            to=arguments["to"].lower(),
            subject=arguments["subject"],
            body=arguments["body"],
        )
        return effect.result
