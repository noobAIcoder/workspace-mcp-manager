from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


PORT_PROJECTION_VERSION = 1


class ListenerState(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int
    purpose: str
    source: str
    instance_id: str | None = None
    attribution: str | None = None
    dual_stack_unknown: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "bind_address": self.host,
            "port": self.port,
            "source": self.source,
            "purpose": self.purpose,
            "instance_id": self.instance_id,
            "attribution": self.attribution,
            "collision_relevance": True,
        }


@dataclass(frozen=True, slots=True)
class ListenerObservation:
    state: ListenerState
    endpoints: tuple[Endpoint, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "listeners": [item.to_dict() for item in self.endpoints],
        }


def _normalized_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    value = host.strip().lower()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if value == "*":
        return None
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def normalized_host(host: str) -> str:
    value = host.strip().lower()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if value == "*":
        return "0.0.0.0"
    parsed = _normalized_ip(value)
    return str(parsed) if parsed is not None else value


def endpoint_overlap(left: Endpoint, right: Endpoint) -> bool | None:
    """Return True/False when overlap is knowable locally, otherwise None."""

    if left.port != right.port:
        return False

    left_host = normalized_host(left.host)
    right_host = normalized_host(right.host)
    if left_host == right_host:
        return True

    left_ip = _normalized_ip(left_host)
    right_ip = _normalized_ip(right_host)
    if left_ip is None or right_ip is None:
        # Non-IP bind names are never resolved here. Different names therefore
        # have an unknown relationship rather than being claimed clear.
        return None

    if isinstance(left_ip, ipaddress.IPv4Address) and isinstance(right_ip, ipaddress.IPv4Address):
        return str(left_ip) == "0.0.0.0" or str(right_ip) == "0.0.0.0"

    if isinstance(left_ip, ipaddress.IPv6Address) and isinstance(right_ip, ipaddress.IPv6Address):
        return str(left_ip) == "::" or str(right_ip) == "::"

    # IPv4 and IPv6 are distinct unless the observed IPv6 wildcard has unknown
    # IPV6_V6ONLY semantics, in which case PM3.1 conservatively treats it as an
    # overlap with IPv4 on the same TCP port.
    ipv6_endpoint = left if isinstance(left_ip, ipaddress.IPv6Address) else right
    ipv6_ip = left_ip if isinstance(left_ip, ipaddress.IPv6Address) else right_ip
    if str(ipv6_ip) == "::" and ipv6_endpoint.dual_stack_unknown:
        return True
    return False


def endpoint_conflicts(candidate: Endpoint, others: Iterable[Endpoint]) -> tuple[list[Endpoint], bool]:
    conflicts: list[Endpoint] = []
    unknown = False
    for item in others:
        overlap = endpoint_overlap(candidate, item)
        if overlap is True:
            conflicts.append(item)
        elif overlap is None:
            unknown = True
    return conflicts, unknown


def _split_ss_local(value: str) -> tuple[str, int] | None:
    text = value.strip()
    if not text:
        return None
    if text.startswith("["):
        close = text.rfind("]:")
        if close == -1:
            return None
        host = text[1:close]
        port_text = text[close + 2 :]
    else:
        host, separator, port_text = text.rpartition(":")
        if not separator:
            return None
    if port_text == "*" or not port_text.isdigit():
        return None
    port = int(port_text)
    if not 1 <= port <= 65535:
        return None
    return (host or "*", port)


def observe_tcp_listeners(*, timeout: float = 3.0) -> ListenerObservation:
    try:
        completed = subprocess.run(
            ["ss", "-H", "-ltn"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ListenerObservation(ListenerState.UNAVAILABLE, (), f"ss unavailable: {exc}")
    if completed.returncode != 0:
        detail = completed.stderr.strip()[:512] or f"ss exited {completed.returncode}"
        return ListenerObservation(ListenerState.UNAVAILABLE, (), detail)

    endpoints: list[Endpoint] = []
    malformed = 0
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    for line in lines:
        fields = line.split()
        # `ss -H -ltn` local-address:port is field 4 for normal output.
        if len(fields) < 4:
            malformed += 1
            continue
        parsed = _split_ss_local(fields[3])
        if parsed is None:
            malformed += 1
            continue
        host, port = parsed
        normalized = normalized_host(host)
        endpoints.append(
            Endpoint(
                host=normalized,
                port=port,
                purpose="Unknown",
                source="observed",
                dual_stack_unknown=normalized == "::",
            )
        )
    if malformed:
        return ListenerObservation(
            ListenerState.PARTIAL,
            tuple(endpoints),
            f"parsed {len(endpoints)} listener(s); {malformed} line(s) were not understood",
        )
    return ListenerObservation(ListenerState.AVAILABLE, tuple(endpoints), None)


def listener_strings(observation: ListenerObservation) -> set[str]:
    result: set[str] = set()
    for item in observation.endpoints:
        host = normalized_host(item.host)
        result.add(f"[{host}]:{item.port}" if ":" in host else f"{host}:{item.port}")
    return result
