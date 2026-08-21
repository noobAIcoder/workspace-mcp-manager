from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .domain import DesiredInstance
from .paths import ManagerPaths
from .redaction import sanitized_subprocess_env


DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION = 1
PACKAGE_JSON_LIMIT = 256 * 1024

_SEMVER_RE = re.compile(r"^v?([0-9]+)(?:\.([0-9]+))?(?:\.([0-9]+))?(?:[-+][0-9A-Za-z.-]+)?$")
_PACKAGE_MANAGER_RE = re.compile(r"^([A-Za-z0-9._-]+)@([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)$")
_COMPARATOR_RE = re.compile(r"^(>=|<=|>|<|=)?(v?[0-9]+(?:\.[0-9]+){0,2})$")
_WILDCARD_RE = re.compile(r"^v?([0-9]+)\.(?:x|X|\*)$")


@dataclass(frozen=True, order=True, slots=True)
class SemVer:
    major: int
    minor: int = 0
    patch: int = 0

    def text(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_semver(value: str | None) -> SemVer | None:
    if not isinstance(value, str):
        return None
    match = _SEMVER_RE.fullmatch(value.strip())
    if match is None:
        return None
    return SemVer(int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0))


def node_range_satisfied(requirement: str, version: SemVer) -> bool | None:
    text = requirement.strip()
    if not text or "||" in text or " - " in text or any(marker in text for marker in ("^", "~")):
        return None
    tokens = text.split()
    if not tokens:
        return None
    for token in tokens:
        wildcard = _WILDCARD_RE.fullmatch(token)
        if wildcard is not None:
            if version.major != int(wildcard.group(1)):
                return False
            continue
        match = _COMPARATOR_RE.fullmatch(token)
        if match is None:
            return None
        operator = match.group(1) or "="
        target = parse_semver(match.group(2))
        if target is None:
            return None
        if operator == ">=" and not version >= target:
            return False
        if operator == ">" and not version > target:
            return False
        if operator == "<=" and not version <= target:
            return False
        if operator == "<" and not version < target:
            return False
        if operator == "=" and not version == target:
            return False
    return True


def _bounded_version(
    executable: Path,
    *,
    cwd: Path,
    env: Mapping[str, str],
    args: Sequence[str] = ("--version",),
) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable), *args],
            cwd=str(cwd),
            env=sanitized_subprocess_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=4.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    return text.splitlines()[0][:256] if text else None


def _regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _read_package_json(workspace: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = workspace / "package.json"
    if not path.exists():
        return None, None
    if not _regular_file(path):
        return None, "PACKAGE_JSON_INVALID"
    try:
        if path.stat().st_size > PACKAGE_JSON_LIMIT:
            return None, "PACKAGE_JSON_INVALID"
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "PACKAGE_JSON_INVALID"
    return (value, None) if isinstance(value, dict) else (None, "PACKAGE_JSON_INVALID")


def _package_requirements(package: Mapping[str, Any]) -> dict[str, Any]:
    engines = package.get("engines") if isinstance(package.get("engines"), Mapping) else {}
    node_requirement = engines.get("node") if isinstance(engines.get("node"), str) else None
    package_manager_raw = package.get("packageManager") if isinstance(package.get("packageManager"), str) else None
    package_manager: dict[str, Any] | None = None
    if package_manager_raw:
        match = _PACKAGE_MANAGER_RE.fullmatch(package_manager_raw.strip())
        if match:
            package_manager = {
                "name": match.group(1),
                "version": match.group(2),
                "source": "package.json#packageManager",
                "supported": match.group(1) == "pnpm",
            }
        else:
            package_manager = {
                "name": None,
                "version": None,
                "source": "package.json#packageManager",
                "supported": False,
                "raw": package_manager_raw[:256],
            }
    return {
        "node": {
            "range": node_requirement,
            "source": "package.json#engines.node" if node_requirement else None,
        },
        "package_manager": package_manager,
    }


def _nvm_roots(paths: ManagerPaths, workspace: Path, requirements: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = paths.account_home / ".nvm" / "versions" / "node"
    try:
        children = [item for item in root.iterdir() if item.is_dir() and not item.is_symlink()]
    except OSError:
        children = []
    results: list[dict[str, Any]] = []
    node_req = requirements.get("node") if isinstance(requirements.get("node"), Mapping) else {}
    node_range = node_req.get("range") if isinstance(node_req.get("range"), str) else None
    pm = requirements.get("package_manager") if isinstance(requirements.get("package_manager"), Mapping) else None
    for node_root in children[:64]:
        bin_dir = node_root / "bin"
        env = dict(os.environ)
        env["HOME"] = str(paths.account_home)
        env["PATH"] = os.pathsep.join((str(bin_dir), "/usr/local/bin", "/usr/bin", "/bin"))
        # Discovery is observational. A Corepack-managed pnpm shim must not
        # download a package-manager release merely because the manager probes
        # `pnpm --version`.
        env["COREPACK_ENABLE_NETWORK"] = "0"
        env["COREPACK_DEFAULT_TO_LATEST"] = "0"
        node_raw = _bounded_version(bin_dir / "node", cwd=workspace, env=env)
        node_version = parse_semver(node_raw)
        node_compatible = node_range_satisfied(node_range, node_version) if node_range and node_version else None
        pnpm_raw = _bounded_version(bin_dir / "pnpm", cwd=workspace, env=env)
        npm_raw = _bounded_version(bin_dir / "npm", cwd=workspace, env=env)
        corepack_raw = _bounded_version(bin_dir / "corepack", cwd=workspace, env=env)
        pm_compatible: bool | None = None
        if pm is not None and pm.get("supported") and pm.get("name") == "pnpm":
            pm_compatible = pnpm_raw == pm.get("version")
        elif pm is None:
            pm_compatible = True
        results.append(
            {
                "root": str(node_root),
                "bin": str(bin_dir),
                "node": {"version": node_version.text() if node_version else None, "raw": node_raw, "compatible": node_compatible},
                "npm": {"version": npm_raw},
                "corepack": {"version": corepack_raw},
                "pnpm": {"version": pnpm_raw, "compatible": pm_compatible},
            }
        )
    return sorted(
        results,
        key=lambda item: parse_semver(str(item.get("node", {}).get("version") or "")) or SemVer(0, 0, 0),
        reverse=True,
    )


def recommended_toolchain_values(
    paths: ManagerPaths,
    *,
    node_root: str,
    current_exec_path: str,
    current_external_roots: Sequence[str],
) -> tuple[str, list[str]]:
    selected_root = Path(node_root)
    selected_bin = str(selected_root / "bin")
    nvm_versions = paths.account_home / ".nvm" / "versions" / "node"
    path_entries: list[str] = []
    for entry in current_exec_path.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry)
        if candidate.parent.parent == nvm_versions and candidate.name == "bin":
            continue
        if entry not in path_entries:
            path_entries.append(entry)
    path_entries.insert(0, selected_bin)

    roots: list[str] = []
    for entry in current_external_roots:
        candidate = Path(entry)
        if candidate.parent == nvm_versions:
            continue
        if entry not in roots:
            roots.append(entry)
    roots.insert(0, str(selected_root))
    return os.pathsep.join(path_entries), roots


def project_development_toolchain(
    paths: ManagerPaths,
    workspace: Path,
    *,
    desired: DesiredInstance | None = None,
) -> dict[str, Any]:
    package, package_error = _read_package_json(workspace)
    if package is None and package_error is None:
        return {
            "development_toolchain_projection_version": DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION,
            "kind": "none",
            "requirements": {},
            "installed": {"node_roots": []},
            "selection": {"state": "not_applicable", "reason_code": "NO_NODE_REQUIREMENT"},
            "declaration": {"state": "not_applicable" if desired is None else "unknown"},
            "warnings": [],
        }
    if package is None:
        return {
            "development_toolchain_projection_version": DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION,
            "kind": "node",
            "requirements": {},
            "installed": {"node_roots": []},
            "selection": {"state": "setup_required", "reason_code": package_error},
            "declaration": {"state": "not_ready" if desired is not None else "unknown"},
            "warnings": ["Repository package.json could not be safely interpreted"],
        }

    requirements = _package_requirements(package)
    node_req = requirements["node"]
    node_range = node_req.get("range")
    package_manager = requirements.get("package_manager")
    if isinstance(package_manager, Mapping) and package_manager.get("supported") is False:
        return {
            "development_toolchain_projection_version": DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION,
            "kind": "node",
            "requirements": requirements,
            "installed": {"node_roots": []},
            "selection": {"state": "setup_required", "reason_code": "PACKAGE_MANAGER_UNAVAILABLE"},
            "declaration": {"state": "not_ready" if desired is not None else "unknown"},
            "warnings": ["Unsupported packageManager declaration; automatic package-manager selection is unavailable"],
        }
    if not isinstance(node_range, str) or not node_range.strip():
        return {
            "development_toolchain_projection_version": DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION,
            "kind": "node",
            "requirements": requirements,
            "installed": {"node_roots": []},
            "selection": {"state": "setup_required", "reason_code": "NO_NODE_REQUIREMENT"},
            "declaration": {"state": "not_ready" if desired is not None else "unknown"},
            "warnings": ["package.json does not declare engines.node; automatic Node selection is unavailable"],
        }
    if node_range_satisfied(node_range, SemVer(0, 0, 0)) is None:
        return {
            "development_toolchain_projection_version": DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION,
            "kind": "node",
            "requirements": requirements,
            "installed": {"node_roots": []},
            "selection": {"state": "setup_required", "reason_code": "NODE_RANGE_UNSUPPORTED"},
            "declaration": {"state": "not_ready" if desired is not None else "unknown"},
            "warnings": [f"Unsupported Node range syntax: {node_range}"],
        }

    roots = _nvm_roots(paths, workspace, requirements)
    fully_compatible = [
        item
        for item in roots
        if item.get("node", {}).get("compatible") is True and item.get("pnpm", {}).get("compatible") is not False
    ]
    node_compatible = [item for item in roots if item.get("node", {}).get("compatible") is True]
    selected = fully_compatible[0] if fully_compatible else None
    if selected is not None:
        state = "ready"
        reason = "READY"
    elif node_compatible:
        pm = requirements.get("package_manager")
        observed = node_compatible[0].get("pnpm", {}).get("version")
        if isinstance(pm, Mapping) and pm.get("name") == "pnpm" and observed is None:
            reason = "PACKAGE_MANAGER_UNAVAILABLE"
        else:
            reason = "PACKAGE_MANAGER_VERSION_MISMATCH"
        state = "setup_required"
    else:
        state = "setup_required"
        reason = "NODE_VERSION_UNAVAILABLE"

    selection: dict[str, Any] = {"state": state, "reason_code": reason}
    if selected is not None:
        selection.update(
            {
                "node_root": selected["root"],
                "node_bin": selected["bin"],
                "node_version": selected["node"]["version"],
                "package_manager": requirements.get("package_manager"),
                "package_manager_version": selected["pnpm"]["version"],
            }
        )

    declaration: dict[str, Any] = {"state": "unknown"}
    if desired is not None:
        if selected is None:
            declaration = {"state": "not_ready", "reason_code": reason}
        else:
            exec_path, external_roots = recommended_toolchain_values(
                paths,
                node_root=str(selected["root"]),
                current_exec_path=desired.mcp.exec_path,
                current_external_roots=desired.mcp.external_roots,
            )
            ready = exec_path == desired.mcp.exec_path and external_roots == list(desired.mcp.external_roots)
            declaration = {
                "state": "ready" if ready else "not_ready",
                "reason_code": "READY" if ready else "DECLARATION_TOOLCHAIN_MISMATCH",
                "recommended_exec_path": exec_path,
                "recommended_external_roots": external_roots,
            }

    warnings: list[str] = []
    if state != "ready":
        warnings.append(f"Development toolchain setup required: {reason}")
    elif desired is not None and declaration.get("state") != "ready":
        warnings.append("Compatible host toolchain exists but the instance declaration does not select it")
    return {
        "development_toolchain_projection_version": DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION,
        "kind": "node",
        "requirements": requirements,
        "installed": {"node_roots": roots},
        "selection": selection,
        "declaration": declaration,
        "warnings": warnings,
    }


__all__ = [
    "DEVELOPMENT_TOOLCHAIN_PROJECTION_VERSION",
    "SemVer",
    "node_range_satisfied",
    "parse_semver",
    "project_development_toolchain",
    "recommended_toolchain_values",
]
