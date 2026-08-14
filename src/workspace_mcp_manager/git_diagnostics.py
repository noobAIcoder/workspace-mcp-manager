from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .development_environment import development_subprocess_env, effective_ssh_auth_sock
from .domain import DesiredInstance
from .paths import ManagerPaths
from .redaction import redact_text


@dataclass(frozen=True, slots=True)
class ProbeResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


def _remote_metadata(url: str) -> dict[str, str | None]:
    value = url.strip()
    if not value:
        return {"transport": None, "host": None}
    if "://" in value:
        parsed = urlparse(value)
        return {
            "transport": parsed.scheme or None,
            "host": parsed.hostname,
        }
    if ":" in value and not value.startswith(("/", "./", "../")):
        left = value.split(":", 1)[0]
        host = left.rsplit("@", 1)[-1]
        return {"transport": "ssh", "host": host or None}
    return {"transport": "path", "host": None}


class GitDiagnosticService:
    """Non-mutating Git/GitHub diagnostics for one desired workspace."""

    def __init__(self, paths: ManagerPaths) -> None:
        self.paths = paths

    def _environment(self, desired: DesiredInstance) -> dict[str, str]:
        return development_subprocess_env(self.paths, desired)

    @staticmethod
    def _which(name: str, desired: DesiredInstance) -> str | None:
        return shutil.which(name, path=desired.mcp.exec_path)

    @staticmethod
    def _run(argv: Sequence[str], env: dict[str, str], *, timeout: float = 20.0) -> ProbeResult:
        try:
            completed = subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ProbeResult(tuple(argv), None, "", "", redact_text(str(exc)))
        return ProbeResult(
            tuple(argv),
            completed.returncode,
            redact_text(completed.stdout[-8192:]),
            redact_text(completed.stderr[-8192:]),
        )

    @staticmethod
    def _check(name: str, status: str, detail: str | None = None) -> dict[str, str | None]:
        return {"name": name, "status": status, "detail": detail}

    def run(self, desired: DesiredInstance) -> dict[str, Any]:
        workspace = Path(desired.workspace_path)
        env = self._environment(desired)
        checks: list[dict[str, str | None]] = []

        git = self._which("git", desired)
        gh = self._which("gh", desired)
        checks.append(self._check("git-executable", "PASS" if git else "FAIL", git))
        if not git:
            return {
                "ok": False,
                "instance_id": desired.instance_id.value,
                "workspace": desired.workspace_path,
                "environment": {
                    "home": str(self.paths.account_home),
                    "path": desired.mcp.exec_path,
                    "gh_config_dir": desired.github.config_dir,
                    "ssh_auth_sock": effective_ssh_auth_sock(desired),
                },
                "repository": {"present": False},
                "working_tree": {"clean": None, "entry_count": None},
                "remote": {"configured": False, "name": None, "reachable": None},
                "github_cli": {"present": bool(gh), "authenticated": None},
                "identity": {
                    "name_present": None,
                    "email_present": None,
                    "name": None,
                    "email": None,
                },
                "checks": checks,
            }

        repo = self._run((git, "-C", str(workspace), "rev-parse", "--is-inside-work-tree"), env)
        repository_present = repo.ok and repo.stdout.strip() == "true"
        checks.append(
            self._check(
                "git-repository",
                "PASS" if repository_present else "FAIL",
                None if repository_present else "workspace is not a Git working tree",
            )
        )

        clean: bool | None = None
        entry_count: int | None = None
        remotes: list[str] = []
        identity_name: str | None = None
        identity_email: str | None = None
        remote_payload: dict[str, Any] = {
            "configured": False,
            "name": None,
            "transport": None,
            "host": None,
            "reachable": None,
            "exit_code": None,
        }

        if repository_present:
            status = self._run(
                (git, "-C", str(workspace), "status", "--porcelain=v1", "--untracked-files=all"),
                env,
            )
            if status.ok:
                lines = [line for line in status.stdout.splitlines() if line]
                clean = not lines
                entry_count = len(lines)
                checks.append(self._check("git-working-tree", "PASS", "clean" if clean else f"dirty entries={entry_count}"))
            else:
                checks.append(self._check("git-working-tree", "FAIL", "git status failed"))

            remote_list = self._run((git, "-C", str(workspace), "remote"), env)
            if remote_list.ok:
                remotes = sorted({line.strip() for line in remote_list.stdout.splitlines() if line.strip()})
            remote_name = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
            if remote_name:
                remote_payload["configured"] = True
                remote_payload["name"] = remote_name
                remote_url = self._run((git, "-C", str(workspace), "remote", "get-url", remote_name), env)
                if remote_url.ok:
                    remote_payload.update(_remote_metadata(remote_url.stdout))
                remote_probe = self._run(
                    (git, "-C", str(workspace), "ls-remote", "--exit-code", remote_name, "HEAD"),
                    env,
                    timeout=30.0,
                )
                remote_payload["reachable"] = remote_probe.ok
                remote_payload["exit_code"] = remote_probe.exit_code
                checks.append(
                    self._check(
                        "git-remote-access",
                        "PASS" if remote_probe.ok else "FAIL",
                        None if remote_probe.ok else "git ls-remote failed",
                    )
                )
            else:
                checks.append(self._check("git-remote-access", "WARN", "no Git remote is configured"))

            name_probe = self._run((git, "-C", str(workspace), "config", "--get", "user.name"), env)
            email_probe = self._run((git, "-C", str(workspace), "config", "--get", "user.email"), env)
            identity_name = name_probe.stdout.strip() if name_probe.ok and name_probe.stdout.strip() else None
            identity_email = email_probe.stdout.strip() if email_probe.ok and email_probe.stdout.strip() else None
            identity_status = "PASS" if identity_name and identity_email else "WARN"
            checks.append(
                self._check(
                    "git-identity",
                    identity_status,
                    "name and email configured" if identity_status == "PASS" else "Git user.name and/or user.email is not configured",
                )
            )

        gh_authenticated: bool | None = None
        if gh:
            gh_probe = self._run((gh, "auth", "status"), env)
            gh_authenticated = gh_probe.ok
            checks.append(
                self._check(
                    "github-cli-auth",
                    "PASS" if gh_authenticated else "WARN",
                    "authenticated" if gh_authenticated else "GitHub CLI authentication is unavailable",
                )
            )
        else:
            checks.append(self._check("github-cli-auth", "WARN", "gh is not present on configured PATH"))

        return {
            "ok": not any(item["status"] == "FAIL" for item in checks),
            "instance_id": desired.instance_id.value,
            "workspace": desired.workspace_path,
            "environment": {
                "home": str(self.paths.account_home),
                "path": desired.mcp.exec_path,
                "gh_config_dir": desired.github.config_dir,
                "ssh_auth_sock": effective_ssh_auth_sock(desired),
            },
            "repository": {"present": repository_present},
            "working_tree": {"clean": clean, "entry_count": entry_count},
            "remote": remote_payload,
            "github_cli": {
                "present": bool(gh),
                "path": gh,
                "authenticated": gh_authenticated,
            },
            "identity": {
                "name_present": bool(identity_name),
                "email_present": bool(identity_email),
                "name": identity_name,
                "email": identity_email,
            },
            "checks": checks,
        }
