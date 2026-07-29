"""Capabilities derived only from the immutable, trusted original task."""

from __future__ import annotations

import re
from dataclasses import dataclass


_EMAIL = r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
_EXPLICIT_SEND = re.compile(
    rf"\bsend\s+(?:(?:exactly\s+)?(?:one|1)|an)\s+email\s+to\s+(?P<email>{_EMAIL})",
    flags=re.IGNORECASE,
)


class PolicyDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class EmailCapability:
    recipient: str
    maximum_sends: int = 1
    logical_key: str = "email-slot-0"


@dataclass(frozen=True)
class Capabilities:
    email: EmailCapability | None = None


def derive_capabilities(trusted_task: str) -> Capabilities:
    match = _EXPLICIT_SEND.search(trusted_task)
    if match is None:
        return Capabilities()
    return Capabilities(email=EmailCapability(recipient=match.group("email").lower()))


def authorize_email(capabilities: Capabilities, recipient: str) -> EmailCapability:
    capability = capabilities.email
    if capability is None:
        raise PolicyDeniedError(
            "send_email denied: the original trusted task did not grant email capability"
        )
    if recipient.lower() != capability.recipient:
        raise PolicyDeniedError(
            "send_email denied: recipient differs from the trusted-task grant"
        )
    return capability
