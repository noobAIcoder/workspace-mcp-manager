from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .paths import ManagerPaths


_DISTRIBUTION_NAME = re.compile(r"^dist-[0-9a-f]{40}$")


class DistributionRuntimeError(RuntimeError):
    """The executing manager looks installed but cannot prove its immutable root."""


@dataclass(frozen=True, slots=True)
class RuntimeDefaults:
    distribution_root: Path | None
    coding_tools_mcp: Path
    tunnel_client: Path


def _looks_like_distribution_projection(path: Path) -> bool:
    parts = path.parts
    for index, part in enumerate(parts):
        if part != "releases" or index == 0 or index + 1 >= len(parts):
            continue
        if parts[index - 1] == "workspace-mcp-manager":
            return True
    return False


def executing_distribution_root(*, module_file: Path | None = None) -> Path | None:
    """Resolve the immutable distribution containing this executing module.

    Source execution is an explicit fallback and never follows ``current``.
    A path that already projects through the manager release namespace is treated
    as installed execution and therefore fails closed if its release identity is
    malformed or incomplete.
    """

    source = (module_file or Path(__file__)).resolve()
    try:
        release = source.parents[3]
    except IndexError:
        release = Path("/")

    expected_suffix = Path("manager/src/workspace_mcp_manager") / source.name
    try:
        relative = source.relative_to(release)
    except ValueError:
        relative = Path()

    if relative != expected_suffix or not _DISTRIBUTION_NAME.fullmatch(release.name):
        if _looks_like_distribution_projection(source):
            raise DistributionRuntimeError(
                f"executing manager is under the distribution namespace but has no valid immutable release root: {source}"
            )
        return None

    install_json = release / "install.json"
    try:
        data = json.loads(install_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DistributionRuntimeError(f"cannot validate executing distribution manifest: {install_json}") from exc

    commit = release.name.removeprefix("dist-")
    schema = data.get("schema_version")
    if (
        schema not in (3, 4)
        or data.get("distribution_id") != release.name
        or data.get("release_commit") != commit
    ):
        raise DistributionRuntimeError("executing distribution identity disagrees with install.json")

    if schema == 4:
        if data.get("distribution_profile") != "manager-only":
            raise DistributionRuntimeError("unsupported manager distribution profile")
        if not isinstance(data.get("components"), dict) or set(data["components"]) != {"python", "uv"}:
            raise DistributionRuntimeError("manager-only distribution declares unexpected components")
        required = (
            release / "runtimes/python/bin/python3",
            release / "tools/uv/uv",
        )
    else:
        required = (
            release / "runtimes/coding-tools-mcp/bin/coding-tools-mcp",
            release / "runtimes/tunnel-client/tunnel-client",
            release / "runtimes/python/bin/python3",
        )
    if any(not path.is_file() for path in required):
        raise DistributionRuntimeError("executing distribution is missing a required internal runtime")
    return release


def _external_executable(paths: ManagerPaths, name: str, *, installed: bool) -> Path:
    home = paths.account_home
    stable = (
        home / ".local/opt/coding-tools-mcp/bin/coding-tools-mcp"
        if name == "coding-tools-mcp"
        else home / ".local/opt/tunnel-client/tunnel-client"
    )
    candidates = [stable, home / ".local/bin" / name]
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve(strict=True)
    # Source development retains the old path fallback. Installed manager-only
    # releases guide the operator to stable, manually managed paths.
    return (stable if installed else candidates[1]).resolve(strict=False)


def runtime_defaults(paths: ManagerPaths, *, module_file: Path | None = None) -> RuntimeDefaults:
    release = executing_distribution_root(module_file=module_file)
    if release is None:
        return RuntimeDefaults(
            distribution_root=None,
            coding_tools_mcp=_external_executable(paths, "coding-tools-mcp", installed=False),
            tunnel_client=_external_executable(paths, "tunnel-client", installed=False),
        )
    manifest = json.loads((release / "install.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] == 4:
        return RuntimeDefaults(
            distribution_root=release,
            coding_tools_mcp=_external_executable(paths, "coding-tools-mcp", installed=True),
            tunnel_client=_external_executable(paths, "tunnel-client", installed=True),
        )
    return RuntimeDefaults(
        distribution_root=release,
        coding_tools_mcp=(release / "runtimes/coding-tools-mcp/bin/coding-tools-mcp").resolve(strict=True),
        tunnel_client=(release / "runtimes/tunnel-client/tunnel-client").resolve(strict=True),
    )
