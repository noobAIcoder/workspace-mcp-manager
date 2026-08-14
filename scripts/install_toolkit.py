#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Iterable


MIN_PYTHON = (3, 11)


def die(message: str, *, code: int = 2) -> "None":
    print(f"workspace-mcp-manager installer: {message}", file=sys.stderr)
    raise SystemExit(code)


def atomic_write(path: Path, text: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="strict") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def candidate_files(repo: Path) -> list[Path]:
    paths = [repo / "pyproject.toml"]
    source_root = repo / "src" / "workspace_mcp_manager"
    if not source_root.is_dir():
        die(f"candidate source package is missing: {source_root}")
    paths.extend(sorted(source_root.rglob("*.py")))
    for path in paths:
        if not path.is_file() or path.is_symlink():
            die(f"candidate source file is missing/not regular: {path}")
    return paths


def source_fingerprint(repo: Path) -> str:
    digest = hashlib.sha256()
    for path in candidate_files(repo):
        relative = path.relative_to(repo).as_posix()
        data = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def run_candidate(repo: Path, code: str, *, stdin_text: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src")
    return subprocess.run(
        [sys.executable, "-c", code],
        input=stdin_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
        timeout=20,
    )


def validate_candidate(repo: Path) -> None:
    code = (
        "from workspace_mcp_manager.cli import build_parser; "
        "from workspace_mcp_manager.domain import DesiredInstance; "
        "build_parser(); print('OK')"
    )
    completed = run_candidate(repo, code)
    if completed.returncode != 0 or completed.stdout.strip() != "OK":
        message = (completed.stderr or completed.stdout).strip()[-2000:]
        die(f"candidate manager validation failed: {message}")


def declaration_paths(account_home: Path) -> list[Path]:
    registry = account_home / ".config" / "workspace-mcp-manager" / "instances"
    try:
        return sorted(path for path in registry.glob("*.json") if path.is_file() and not path.is_symlink())
    except OSError as exc:
        die(f"cannot enumerate existing manager declarations: {exc}")


def validate_declarations(repo: Path, declarations: Iterable[Path]) -> tuple[bool, str | None]:
    payloads: list[str] = []
    for path in declarations:
        try:
            payloads.append(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError) as exc:
            return False, f"cannot read declaration {path}: {exc}"
    code = """
import json, sys
from workspace_mcp_manager.domain import DesiredInstance
values = json.loads(sys.stdin.read())
for raw in values:
    DesiredInstance.from_json(raw)
print('OK')
"""
    completed = run_candidate(repo, code, stdin_text=json.dumps(payloads))
    if completed.returncode != 0 or completed.stdout.strip() != "OK":
        return False, (completed.stderr or completed.stdout).strip()[-2000:]
    return True, None


def installed_fingerprint(toolkit_root: Path) -> str | None:
    current = toolkit_root / "current"
    if not current.is_symlink():
        return None
    try:
        resolved = current.resolve(strict=True)
    except OSError:
        return None
    releases = toolkit_root / "releases"
    try:
        relative = resolved.relative_to(releases.resolve())
    except (OSError, ValueError):
        return None
    return relative.parts[0] if len(relative.parts) == 1 else None


def stable_entrypoint(prefix: Path, name: str) -> Path:
    return prefix / "bin" / name


def entrypoints_valid(prefix: Path) -> bool:
    toolkit_root = prefix / "lib" / "workspace-mcp-manager"
    expected = {
        "workspace-mcp-manager": "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager",
        "workspace-mcp-manager-tui": "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager-tui",
        "workspace-mcp-reboot": "../lib/workspace-mcp-manager/current/bin/workspace-mcp-reboot",
    }
    if installed_fingerprint(toolkit_root) is None:
        return False
    for name, target in expected.items():
        path = stable_entrypoint(prefix, name)
        if not path.is_symlink():
            return False
        try:
            if os.readlink(path) != target:
                return False
            if not path.resolve(strict=True).is_file():
                return False
        except OSError:
            return False
    return True


def wrapper_text(release: Path, target: str) -> str:
    python = Path(sys.executable).resolve()
    src = release / "src"
    if target == "manager":
        statement = "from workspace_mcp_manager.cli import main; raise SystemExit(main())"
    elif target == "tui":
        statement = "from workspace_mcp_manager.tui import main; raise SystemExit(main())"
    else:
        statement = "from workspace_mcp_manager.reboot import reboot_bridge_main; raise SystemExit(reboot_bridge_main())"
    return (
        "#!/bin/sh\n"
        f"export PYTHONPATH={sh_quote(str(src))}\n"
        f"exec {sh_quote(str(python))} -c {sh_quote(statement)} \"$@\"\n"
    )


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and not path.is_symlink():
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


def stage_release(repo: Path, releases: Path, fingerprint: str) -> Path:
    final = releases / fingerprint
    if final.exists():
        if not final.is_dir() or final.is_symlink():
            die(f"release path is not a regular directory: {final}")
        return final

    releases.mkdir(parents=True, exist_ok=True)
    stage = releases / f".stage-{fingerprint[:12]}-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    try:
        shutil.copytree(repo / "src", stage / "src", symlinks=False)
        shutil.copy2(repo / "pyproject.toml", stage / "pyproject.toml", follow_symlinks=False)
        (stage / "bin").mkdir(mode=0o755)
        atomic_write(stage / "bin" / "workspace-mcp-manager", wrapper_text(final, "manager"), mode=0o755)
        atomic_write(stage / "bin" / "workspace-mcp-manager-tui", wrapper_text(final, "tui"), mode=0o755)
        atomic_write(stage / "bin" / "workspace-mcp-reboot", wrapper_text(final, "reboot"), mode=0o755)
        atomic_write(
            stage / "install.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "source_fingerprint": fingerprint,
                    "python": str(Path(sys.executable).resolve()),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            mode=0o644,
        )
        fsync_tree(stage)
        os.replace(stage, final)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return final


def switch_current(prefix: Path, toolkit_root: Path, fingerprint: str) -> None:
    current = toolkit_root / "current"
    atomic_symlink(current, f"releases/{fingerprint}")
    expected = {
        "workspace-mcp-manager": "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager",
        "workspace-mcp-manager-tui": "../lib/workspace-mcp-manager/current/bin/workspace-mcp-manager-tui",
        "workspace-mcp-reboot": "../lib/workspace-mcp-manager/current/bin/workspace-mcp-reboot",
    }
    for name, target in expected.items():
        atomic_symlink(stable_entrypoint(prefix, name), target)


def validate_installed(prefix: Path) -> None:
    for name in ("workspace-mcp-manager", "workspace-mcp-manager-tui"):
        executable = stable_entrypoint(prefix, name)
        completed = subprocess.run(
            [str(executable), "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=20,
        )
        if completed.returncode != 0:
            die(f"installed {name} entrypoint validation failed: {(completed.stderr or completed.stdout)[-2000:]}")


def report(*, candidate: str, installed: str | None, instance_count: int, instances_valid: bool, links_valid: bool) -> dict[str, object]:
    return {
        "candidate_fingerprint": candidate,
        "installed_fingerprint": installed,
        "installed": installed is not None,
        "upgrade_required": installed is not None and installed != candidate,
        "instance_count": instance_count,
        "instances_valid": instances_valid,
        "entrypoints_valid": links_valid,
    }


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        die("Python 3.11 or newer is required")
    parser = argparse.ArgumentParser(prog="install_toolkit.py")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--account-home", type=Path, default=Path.home())
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    account_home = args.account_home.resolve()
    prefix = (args.prefix or (account_home / ".local")).resolve()
    if not prefix.is_absolute() or not account_home.is_absolute():
        die("account home and prefix must be absolute")

    validate_candidate(repo)
    fingerprint = source_fingerprint(repo)
    toolkit_root = prefix / "lib" / "workspace-mcp-manager"
    current = installed_fingerprint(toolkit_root)
    declarations = declaration_paths(account_home)
    configs_valid, config_error = validate_declarations(repo, declarations)
    links_valid = entrypoints_valid(prefix)

    if args.check:
        payload = report(
            candidate=fingerprint,
            installed=current,
            instance_count=len(declarations),
            instances_valid=configs_valid,
            links_valid=links_valid,
        )
        if config_error:
            payload["validation_error"] = config_error
        print(json.dumps(payload, sort_keys=True))
        return 0 if configs_valid else 1

    if not configs_valid:
        die(f"existing manager declaration validation failed: {config_error}")

    manager_path = stable_entrypoint(prefix, "workspace-mcp-manager")
    foreign_or_unmanaged = manager_path.exists() or manager_path.is_symlink()
    candidate_is_current = current == fingerprint and links_valid
    if candidate_is_current:
        print(json.dumps(report(
            candidate=fingerprint,
            installed=current,
            instance_count=len(declarations),
            instances_valid=True,
            links_valid=True,
        ), sort_keys=True))
        return 0

    if (current is not None and current != fingerprint) or (current is None and foreign_or_unmanaged):
        if not args.upgrade:
            die("shared toolkit replacement requires explicit --upgrade")

    release = stage_release(repo, toolkit_root / "releases", fingerprint)
    if not release.is_dir():
        die("candidate release staging failed")
    switch_current(prefix, toolkit_root, fingerprint)
    validate_installed(prefix)
    payload = report(
        candidate=fingerprint,
        installed=fingerprint,
        instance_count=len(declarations),
        instances_valid=True,
        links_valid=entrypoints_valid(prefix),
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
