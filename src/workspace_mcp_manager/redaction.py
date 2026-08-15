from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


SECRET_ENV_KEYS = frozenset(
    {
        "CONTROL_PLANE_API_KEY",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GIT_ASKPASS",
    }
)

_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(CONTROL_PLANE_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN|GH_TOKEN|GITHUB_ENTERPRISE_TOKEN|GH_ENTERPRISE_TOKEN)\s*=\s*([^\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def redact_text(value: str) -> str:
    value = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<REDACTED>", value)
    return _BEARER_RE.sub("Bearer <REDACTED>", value)


def sanitized_subprocess_env(source: Mapping[str, str]) -> dict[str, str]:
    result = {key: value for key, value in source.items() if key not in SECRET_ENV_KEYS}
    result.setdefault("LC_ALL", "C")
    return result


def redact_object(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).upper() in SECRET_ENV_KEYS:
                # Metadata may intentionally report only whether a secret is
                # present. Preserve that boolean while never returning a
                # secret-bearing string/object under a secret key.
                result[str(key)] = item if isinstance(item, bool) else "<REDACTED>"
            else:
                result[str(key)] = redact_object(item)
        return result
    if isinstance(value, list):
        return [redact_object(item) for item in value]
    if isinstance(value, tuple):
        return [redact_object(item) for item in value]
    return value

