"""Read-only external runtime inventory and manual installation guidance.

This module never installs, downloads, updates, or configures runtimes. Network
requests happen only when the operator explicitly requests upstream versions.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .paths import ManagerPaths
from .redaction import sanitized_subprocess_env


MCP = "coding-tools-mcp"
TUNNEL = "tunnel-client"
RUNTIMES = (MCP, TUNNEL)
DOCS = {
    MCP: "https://coding-tools-mcp.github.io/docs/getting-started/",
    TUNNEL: "https://github.com/openai/tunnel-client/releases/latest",
}
MCP_COMMANDS = """# Stop affected MCP instances before changing an installation.
sudo apt install -y python3-venv
python3 -m venv "$HOME/.local/opt/coding-tools-mcp"
"$HOME/.local/opt/coding-tools-mcp/bin/python" -m pip install --upgrade coding-tools-mcp
"$HOME/.local/opt/coding-tools-mcp/bin/python" -m pip show coding-tools-mcp
"$HOME/.local/opt/coding-tools-mcp/bin/coding-tools-mcp" --help
"""
TUNNEL_COMMANDS = """# Stop affected tunnels before updating the executable in place.
# Requires GitHub CLI (gh) and unzip.
set -e
case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64) arch=arm64 ;;
  *) echo "Unsupported architecture"; exit 1 ;;
esac
release="$(gh release view -R openai/tunnel-client --json tagName --jq .tagName)"
archive="tunnel-client-${release}-linux-${arch}.zip"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
gh release download "$release" -R openai/tunnel-client -p "$archive" -p SHA256SUMS.txt -D "$tmp"
(cd "$tmp" && grep -F " $archive" SHA256SUMS.txt | sha256sum -c -)
mkdir -p "$HOME/.local/opt/tunnel-client"
unzip -oq "$tmp/$archive" -d "$HOME/.local/opt/tunnel-client"
chmod +x "$HOME/.local/opt/tunnel-client/tunnel-client" "$HOME/.local/opt/tunnel-client/cloudflared"
"$HOME/.local/opt/tunnel-client/tunnel-client" --version
"""
GUIDANCE = {
    MCP: MCP_COMMANDS,
    TUNNEL: TUNNEL_COMMANDS,
}
_UPSTREAM = {
    MCP: "https://pypi.org/pypi/coding-tools-mcp/json",
    TUNNEL: "https://api.github.com/repos/openai/tunnel-client/releases/latest",
}
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:$|\b)")


def _version_tuple(value: str | None) -> tuple[int, int, int] | None:
    match = _VERSION.search(value or "")
    return tuple(map(int, match.groups())) if match else None


class ComponentInventory:
    """Bounded host inspection for the two manually managed runtime programs."""

    def __init__(self, paths: ManagerPaths) -> None:
        self.paths = paths

    def _known_paths(self) -> dict[str, dict[Path, set[str]]]:
        home = self.paths.account_home
        paths: dict[str, dict[Path, set[str]]] = {
            name: defaultdict(set) for name in RUNTIMES
        }

        def add(name: str, raw: str | Path, source: str) -> None:
            path = Path(raw).expanduser()
            if path.is_absolute():
                paths[name][path.resolve(strict=False)].add(source)

        for name in RUNTIMES:
            add(name, home / ".local/bin" / name, "local-bin")
            add(name, home / ".local/opt" / name / (
                "bin/coding-tools-mcp" if name == MCP else "tunnel-client"
            ), "stable-location")
            found = shutil.which(name)
            if found:
                add(name, found, "PATH")

        # An instance declaration is the authoritative reference to its
        # executable, including retained immutable historical distributions.
        try:
            declarations = sorted(self.paths.registry_dir.glob("*.json"))
        except OSError:
            declarations = []
        for declaration in declarations:
            if declaration.is_symlink() or not declaration.is_file():
                continue
            try:
                data = json.loads(declaration.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                identifier = data.get("instance_id")
                if not isinstance(identifier, str) or not identifier:
                    continue
                for name, field in ((MCP, "mcp"), (TUNNEL, "tunnel")):
                    config = data.get(field)
                    if isinstance(config, dict) and isinstance(config.get("binary"), str):
                        add(name, config["binary"], f"instance:{identifier}")
            except (OSError, UnicodeError, ValueError):
                continue

        # Inspect retained bundled distributions without an unrestricted scan.
        release_root = (
            self.paths.manager_executable.parent.parent
            / "lib/workspace-mcp-manager/releases"
        )
        for name, suffix in (
            (MCP, "runtimes/coding-tools-mcp/bin/coding-tools-mcp"),
            (TUNNEL, "runtimes/tunnel-client/tunnel-client"),
        ):
            try:
                matches = sorted(release_root.glob(f"dist-*/{suffix}"))
            except OSError:
                matches = []
            for candidate in matches:
                add(name, candidate, "historical-distribution")
        return paths

    @staticmethod
    def _probe(argv: list[str]) -> str | None:
        try:
            result = subprocess.run(
                argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                errors="replace", timeout=5, check=False,
                env=sanitized_subprocess_env(os.environ),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return (result.stdout or result.stderr).strip() if result.returncode == 0 else None

    def _inspect(self, name: str, path: Path, sources: set[str]) -> dict[str, Any]:
        used_by = sorted(item[9:] for item in sources if item.startswith("instance:"))
        row: dict[str, Any] = {
            "path": str(path), "version": None, "sources": sorted(sources),
            "used_by": used_by, "status": "missing",
            "compatibility": "not-checked",
        }
        if not path.is_file() or not os.access(path, os.X_OK):
            return row
        row["status"] = "installed"
        if name == TUNNEL:
            observed = self._probe([str(path), "--version"])
            match = _VERSION.search(observed or "")
            if match:
                row["version"] = match.group()
                row["status"] = "verified"
            else:
                row["detail"] = "Version command failed or returned an unknown format"
            row["companion_present"] = (path.parent / "cloudflared").is_file()
            return row

        # A normal isolated Python installation has its Python interpreter in
        # the same bin directory. Old bundled distributions use a separate
        # interpreter and a separate site-packages tree.
        candidates: list[tuple[Path, Path | None]] = [
            (path.parent / "python", None),
            (path.parent / "python3", None),
        ]
        if path.parent.parent.name == MCP and path.parent.name == "bin":
            runtimes = path.parent.parent.parent
            candidates.append((
                runtimes / "python/bin/python3",
                path.parent.parent / "site-packages",
            ))
        for python, site in candidates:
            if not python.is_file():
                continue
            code = "import importlib.metadata as m; print(m.version('coding-tools-mcp'))"
            args = [str(python), "-I", "-c", code]
            if site is not None:
                code = (
                    "import importlib.metadata as m,sys; "
                    "sys.path.insert(0,sys.argv[1]); "
                    "print(m.version('coding-tools-mcp'))"
                )
                args = [str(python), "-I", "-c", code, str(site)]
            version = self._probe(args)
            if version and _version_tuple(version):
                row["version"] = version
                row["status"] = "verified"
                break
        if row["status"] != "verified":
            row["detail"] = "Python package version could not be verified"
        return row

    @staticmethod
    def _latest(name: str) -> tuple[str | None, str | None]:
        request = urllib.request.Request(
            _UPSTREAM[name],
            headers={"User-Agent": "workspace-mcp-manager/component-inspection",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    return None, "Upstream response exceeded inspection limit"
            data = json.loads(body)
            release = data.get("info", {}).get("version") if name == MCP else data.get("tag_name")
            if not isinstance(release, str) or _version_tuple(release) is None:
                return None, "Upstream did not return a stable version"
            return release, None
        except (OSError, UnicodeError, ValueError) as exc:
            return None, f"Upstream unavailable: {type(exc).__name__}"

    def inspect(self, *, check_upstream: bool = False) -> dict[str, Any]:
        paths = self._known_paths()
        components: dict[str, Any] = {}
        for name in RUNTIMES:
            installations = [
                self._inspect(name, path, sources)
                for path, sources in sorted(paths[name].items(), key=lambda item: str(item[0]))
                if path.exists() or any(src.startswith("instance:") for src in sources)
            ]
            result: dict[str, Any] = {
                "installations": installations, "documentation_url": DOCS[name],
                "manual_commands": GUIDANCE[name], "latest_stable": None,
                "upstream_checked": check_upstream, "upstream_error": None,
            }
            if check_upstream:
                result["latest_stable"], result["upstream_error"] = self._latest(name)
                latest = _version_tuple(result["latest_stable"])
                for entry in installations:
                    version = _version_tuple(entry["version"])
                    entry["update_available"] = (
                        latest > version if latest is not None and version is not None else None
                    )
            components[name] = result
        try:
            textual_version = importlib.metadata.version("textual")
        except importlib.metadata.PackageNotFoundError:
            textual_version = None
        components["textual"] = {
            "installations": [], "version": textual_version,
            "status": "managed-with-manager", "documentation_url": None,
            "manual_commands": None, "latest_stable": None,
        }
        return {"ok": True, "schema_version": 1, "components": components}
