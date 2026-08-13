from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

from .access import AccessManager
from .cleanup import LegacyCleanupService
from .codex_jobs import CodexJobManager
from .diagnostics import DEFAULT_SINCE_SECONDS, ResilienceDiagnosticService
from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError
from .generation import ResourceGenerator
from .git_diagnostics import GitDiagnosticService
from .host import HostInspector
from .host_apply import HostExecutionBridge, HostInstanceStatusService
from .lifecycle import HostLifecycleWorker
from .paths import ManagerPaths
from .planning import ReconciliationPlanner
from .reboot import RebootService
from .recovery_planning import project_recoverable_failed_first_apply
from .redaction import redact_object
from .registry import InstanceRegistry
from .runtime import run_admission_guard, run_tunnel


def _emit(value: Any, *, pretty: bool, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    sanitized = redact_object(value)
    json.dump(
        sanitized,
        stream,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    stream.write("\n")


def _payload_exit_code(payload: Any) -> int:
    if isinstance(payload, Mapping) and payload.get("ok") is False:
        return 1
    return 0


def _load_declaration(path: Path) -> DesiredInstance:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ManagerError(ErrorCode.IO_ERROR, f"cannot read declaration: {path}") from exc
    return DesiredInstance.from_json(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workspace-mcp-manager")
    parser.add_argument("--registry-dir", type=Path, help="override desired-state registry directory")
    subparsers = parser.add_subparsers(dest="area", required=True)

    host = subparsers.add_parser("host")
    host_sub = host.add_subparsers(dest="command", required=True)
    for command in ("inspect", "doctor", "components"):
        item = host_sub.add_parser(command)
        item.add_argument("--pretty", action="store_true")
    host_reboot = host_sub.add_parser("reboot")
    host_reboot.add_argument("--reason", required=True)
    host_reboot.add_argument("--pretty", action="store_true")
    host_reboot_check = host_sub.add_parser("reboot-check")
    host_reboot_check.add_argument("--pretty", action="store_true")

    instance = subparsers.add_parser("instance")
    instance_sub = instance.add_subparsers(dest="command", required=True)
    for command in ("validate", "create", "update"):
        item = instance_sub.add_parser(command)
        item.add_argument("file", type=Path)
        item.add_argument("--pretty", action="store_true")
    list_cmd = instance_sub.add_parser("list")
    list_cmd.add_argument("--pretty", action="store_true")
    show_cmd = instance_sub.add_parser("show")
    show_cmd.add_argument("instance_id")
    show_cmd.add_argument("--pretty", action="store_true")

    render_cmd = instance_sub.add_parser("render")
    render_cmd.add_argument("instance_id")
    render_cmd.add_argument("--include-content", action="store_true")
    render_cmd.add_argument("--pretty", action="store_true")

    plan_cmd = instance_sub.add_parser("plan")
    plan_cmd.add_argument("instance_id")
    plan_cmd.add_argument("--pretty", action="store_true")

    status_cmd = instance_sub.add_parser("status")
    status_cmd.add_argument("instance_id")
    status_cmd.add_argument("--pretty", action="store_true")

    logs_cmd = instance_sub.add_parser("logs")
    logs_cmd.add_argument("instance_id")
    logs_cmd.add_argument("--lines", type=int, default=100)
    logs_cmd.add_argument("--pretty", action="store_true")

    git_cmd = instance_sub.add_parser("git")
    git_cmd.add_argument("instance_id")
    git_cmd.add_argument("--pretty", action="store_true")

    diagnose_cmd = instance_sub.add_parser("diagnose")
    diagnose_cmd.add_argument("instance_id")
    diagnose_cmd.add_argument("--since-seconds", type=int, default=DEFAULT_SINCE_SECONDS)
    diagnose_cmd.add_argument("--pretty", action="store_true")

    for command in ("apply", "start", "stop", "restart", "remove"):
        item = instance_sub.add_parser(command)
        item.add_argument("instance_id")
        item.add_argument("--pretty", action="store_true")

    codex = subparsers.add_parser("codex")
    codex_sub = codex.add_subparsers(dest="command", required=True)
    codex_start = codex_sub.add_parser("start")
    codex_start.add_argument("instance_id")
    codex_start.add_argument("--mode", choices=("read", "write"), required=True)
    prompt_group = codex_start.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-stdin", action="store_true")
    codex_start.add_argument("--pretty", action="store_true")
    codex_status = codex_sub.add_parser("status")
    codex_status.add_argument("instance_id")
    codex_status.add_argument("job_id")
    codex_status.add_argument("--pretty", action="store_true")
    codex_output = codex_sub.add_parser("output")
    codex_output.add_argument("instance_id")
    codex_output.add_argument("job_id")
    codex_output.add_argument("--limit-bytes", type=int, default=4096)
    codex_output.add_argument("--pretty", action="store_true")
    codex_cancel = codex_sub.add_parser("cancel")
    codex_cancel.add_argument("instance_id")
    codex_cancel.add_argument("job_id")
    codex_cancel.add_argument("--pretty", action="store_true")

    cleanup = subparsers.add_parser("cleanup")
    cleanup_sub = cleanup.add_subparsers(dest="command", required=True)
    cleanup_audit = cleanup_sub.add_parser("audit")
    cleanup_audit.add_argument("instance_id", nargs="?")
    cleanup_audit.add_argument("--pretty", action="store_true")
    cleanup_execute = cleanup_sub.add_parser("execute")
    cleanup_execute.add_argument("instance_id")
    cleanup_execute.add_argument("--pretty", action="store_true")

    access = subparsers.add_parser("access")
    access_sub = access.add_subparsers(dest="command", required=True)
    access_list = access_sub.add_parser("list")
    access_list.add_argument("instance_id")
    access_list.add_argument("--pretty", action="store_true")
    for command in ("add-ro", "add-rw"):
        item = access_sub.add_parser(command)
        item.add_argument("instance_id")
        item.add_argument("alias")
        item.add_argument("path")
        item.add_argument("--pretty", action="store_true")
    access_remove = access_sub.add_parser("remove")
    access_remove.add_argument("instance_id")
    access_remove.add_argument("alias")
    access_remove.add_argument("--pretty", action="store_true")

    runtime = subparsers.add_parser("_runtime", help=argparse.SUPPRESS)
    runtime_sub = runtime.add_subparsers(dest="command", required=True)
    for command in ("tunnel", "admission-guard"):
        item = runtime_sub.add_parser(command)
        item.add_argument("instance_id")
    runtime_registry = runtime_sub.add_parser("registry-write")
    runtime_registry.add_argument("action", choices=("create", "update"))
    runtime_sub.add_parser("list")
    runtime_show = runtime_sub.add_parser("show")
    runtime_show.add_argument("instance_id")
    runtime_render = runtime_sub.add_parser("render")
    runtime_render.add_argument("instance_id")
    runtime_render.add_argument("--include-content", action="store_true")
    runtime_plan = runtime_sub.add_parser("plan")
    runtime_plan.add_argument("instance_id")
    runtime_status = runtime_sub.add_parser("status")
    runtime_status.add_argument("instance_id")
    runtime_logs = runtime_sub.add_parser("logs")
    runtime_logs.add_argument("instance_id")
    runtime_logs.add_argument("--lines", type=int, default=100)
    runtime_git = runtime_sub.add_parser("git")
    runtime_git.add_argument("instance_id")
    runtime_cleanup = runtime_sub.add_parser("cleanup")
    runtime_cleanup.add_argument("action", choices=("audit", "execute"))
    runtime_cleanup.add_argument("instance_id", nargs="?")
    runtime_sub.add_parser("reboot-request")
    runtime_sub.add_parser("reboot-check")
    runtime_diagnose = runtime_sub.add_parser("diagnose")
    runtime_diagnose.add_argument("instance_id")
    runtime_diagnose.add_argument("--since-seconds", type=int, default=DEFAULT_SINCE_SECONDS)
    runtime_access = runtime_sub.add_parser("access")
    runtime_access.add_argument("action", choices=("list", "add-ro", "add-rw", "remove"))
    runtime_access.add_argument("instance_id")
    runtime_access.add_argument("alias", nargs="?")
    runtime_access.add_argument("path", nargs="?")
    runtime_codex_start = runtime_sub.add_parser("codex-start")
    runtime_codex_start.add_argument("mode", choices=("read", "write"))
    runtime_codex_start.add_argument("instance_id")
    runtime_codex_status = runtime_sub.add_parser("codex-status")
    runtime_codex_status.add_argument("instance_id")
    runtime_codex_status.add_argument("job_id")
    runtime_codex_output = runtime_sub.add_parser("codex-output")
    runtime_codex_output.add_argument("instance_id")
    runtime_codex_output.add_argument("job_id")
    runtime_codex_output.add_argument("--limit-bytes", type=int, default=4096)
    runtime_codex_cancel = runtime_sub.add_parser("codex-cancel")
    runtime_codex_cancel.add_argument("instance_id")
    runtime_codex_cancel.add_argument("job_id")
    runtime_codex_worker = runtime_sub.add_parser("codex-worker")
    runtime_codex_worker.add_argument("instance_id")
    runtime_codex_worker.add_argument("job_id")
    runtime_lifecycle = runtime_sub.add_parser("lifecycle")
    runtime_lifecycle.add_argument("action", choices=("apply", "start", "stop", "restart", "remove"))
    runtime_lifecycle.add_argument("instance_id")
    return parser


def _run_host(args: argparse.Namespace, paths: ManagerPaths) -> dict[str, Any]:
    if args.command == "reboot":
        return HostExecutionBridge(paths).run_reboot(reason=args.reason)
    if args.command == "reboot-check":
        return HostExecutionBridge(paths).run_reboot_check()
    inspector = HostInspector(paths)
    if args.command == "inspect":
        return inspector.inspect()
    if args.command == "doctor":
        return inspector.doctor()
    if args.command == "components":
        return inspector.components()
    raise AssertionError(args.command)


def _run_instance(
    args: argparse.Namespace,
    registry: InstanceRegistry,
    paths: ManagerPaths,
) -> dict[str, Any]:
    bridge = HostExecutionBridge(paths)
    if args.command in {"validate", "create", "update"}:
        desired = _load_declaration(args.file)
        if args.command == "validate":
            return {
                "ok": True,
                "instance_id": desired.instance_id.value,
                "fingerprint": desired.fingerprint(),
                "desired": desired.to_dict(),
            }
        # Registry state lives under the real account home. Persist it through
        # the same narrow user-systemd host boundary as lifecycle operations so
        # MCP callers do not require broad home-directory write access.
        return bridge.run_registry_write(args.command, desired)
    if args.command == "list":
        return bridge.run_list()
    if args.command == "show":
        return bridge.run_show(args.instance_id)
    if args.command == "render":
        return bridge.run_render(args.instance_id, include_content=args.include_content)
    if args.command == "plan":
        return bridge.run_plan(args.instance_id)
    if args.command == "status":
        return bridge.run_status(args.instance_id)
    if args.command == "logs":
        return bridge.run_logs(args.instance_id, lines=args.lines)
    if args.command == "git":
        return bridge.run_git(args.instance_id)
    if args.command == "diagnose":
        return bridge.run_diagnose(args.instance_id, since_seconds=args.since_seconds)
    if args.command in {"apply", "start", "stop", "restart", "remove"}:
        return bridge.run_lifecycle(args.command, args.instance_id)
    raise AssertionError(args.command)


def _run_access(args: argparse.Namespace, paths: ManagerPaths) -> dict[str, Any]:
    bridge = HostExecutionBridge(paths)
    return bridge.run_access(
        args.command,
        args.instance_id,
        alias=getattr(args, "alias", None),
        path=getattr(args, "path", None),
    )


def _run_codex(args: argparse.Namespace, paths: ManagerPaths) -> dict[str, Any]:
    bridge = HostExecutionBridge(paths)
    if args.command == "start":
        prompt = args.prompt if args.prompt is not None else sys.stdin.read()
        return bridge.run_codex_start(args.mode, args.instance_id, prompt=prompt)
    if args.command == "status":
        return bridge.run_codex_status(args.instance_id, args.job_id)
    if args.command == "output":
        return bridge.run_codex_output(
            args.instance_id,
            args.job_id,
            limit_bytes=args.limit_bytes,
        )
    if args.command == "cancel":
        return bridge.run_codex_cancel(args.instance_id, args.job_id)
    raise AssertionError(args.command)


def _run_cleanup(args: argparse.Namespace, paths: ManagerPaths) -> dict[str, Any]:
    return HostExecutionBridge(paths).run_cleanup(args.command, instance_id=args.instance_id)


def _run_runtime(
    args: argparse.Namespace,
    registry: InstanceRegistry,
    paths: ManagerPaths,
) -> dict[str, Any] | None:
    if args.command == "registry-write":
        text = sys.stdin.read()
        desired = DesiredInstance.from_json(text)
        path = registry.create(desired) if args.action == "create" else registry.update(desired)
        return {
            "ok": True,
            "action": args.action,
            "instance_id": desired.instance_id.value,
            "fingerprint": desired.fingerprint(),
            "path": str(path),
        }
    if args.command == "list":
        instances = registry.list()
        return {
            "ok": True,
            "instances": [
                {
                    "instance_id": item.instance_id.value,
                    "fingerprint": item.fingerprint(),
                    "lifecycle": item.lifecycle.to_dict(),
                }
                for item in instances
            ],
        }
    if args.command == "show":
        desired = registry.get(args.instance_id)
        return {
            "ok": True,
            "instance_id": desired.instance_id.value,
            "fingerprint": desired.fingerprint(),
            "desired": desired.to_dict(),
        }
    if args.command == "render":
        desired = registry.get(args.instance_id)
        bundle = ResourceGenerator(paths).generate(desired)
        return {"ok": True, **bundle.to_dict(include_content=args.include_content)}
    if args.command == "access":
        access = AccessManager(paths, registry)
        if args.action == "list":
            if args.alias is not None or args.path is not None:
                raise ManagerError(ErrorCode.CONFIG_INVALID, "list does not accept alias or path")
            return access.list(args.instance_id)
        if args.action in {"add-ro", "add-rw"}:
            if args.alias is None or args.path is None:
                raise ManagerError(ErrorCode.CONFIG_INVALID, f"{args.action} requires alias and path")
            return access.add(
                args.instance_id,
                mode="ro" if args.action == "add-ro" else "rw",
                alias=args.alias,
                path=args.path,
            )
        if args.action == "remove":
            if args.alias is None or args.path is not None:
                raise ManagerError(ErrorCode.CONFIG_INVALID, "remove requires exactly one alias")
            return access.remove(args.instance_id, alias=args.alias)
        raise AssertionError(args.action)

    if args.command == "codex-start":
        return CodexJobManager(paths, registry).start(
            args.instance_id,
            mode=args.mode,
            prompt=sys.stdin.read(),
        )
    if args.command == "codex-status":
        return CodexJobManager(paths, registry).status(args.instance_id, args.job_id)
    if args.command == "codex-output":
        return CodexJobManager(paths, registry).output(
            args.instance_id,
            args.job_id,
            limit_bytes=args.limit_bytes,
        )
    if args.command == "codex-cancel":
        return CodexJobManager(paths, registry).cancel(args.instance_id, args.job_id)
    if args.command == "codex-worker":
        CodexJobManager(paths, registry).run_worker(args.instance_id, args.job_id)
        return None

    if args.command == "reboot-request":
        return RebootService(paths, registry).request(reason=sys.stdin.read())
    if args.command == "reboot-check":
        return RebootService(paths, registry).check()
    if args.command == "cleanup":
        service = LegacyCleanupService(paths, registry)
        if args.action == "audit":
            payload = service.audit(args.instance_id)
            if args.instance_id is None:
                candidates = [
                    candidate
                    for candidate in payload.get("candidates", [])
                    if any(
                        resource.get("ownership") == "legacy-owned"
                        for resource in candidate.get("resources", [])
                    )
                ]
                payload["candidates"] = candidates
                payload["ok"] = not any(candidate.get("state") == "conflict" for candidate in candidates)
            return payload
        if args.instance_id is None:
            raise ManagerError(ErrorCode.CONFIG_INVALID, "cleanup execute requires an instance ID")
        return service.apply(args.instance_id)

    desired = registry.get(args.instance_id)
    if args.command == "tunnel":
        run_tunnel(desired, paths)
        return None
    if args.command == "admission-guard":
        run_admission_guard(desired, paths)
        return None
    if args.command == "plan":
        generator = ResourceGenerator(paths)
        bundle = generator.generate(desired)
        plan = ReconciliationPlanner(paths, registry, generator=generator).plan(desired)
        plan = project_recoverable_failed_first_apply(paths, desired, bundle, plan)
        return {"ok": plan.valid, **plan.to_dict()}
    if args.command == "status":
        return HostInstanceStatusService(paths, registry).status(args.instance_id)
    if args.command == "logs":
        return HostInstanceStatusService(paths, registry).logs(args.instance_id, lines=args.lines)
    if args.command == "git":
        return GitDiagnosticService(paths).run(desired)
    if args.command == "diagnose":
        return ResilienceDiagnosticService(paths).run(desired, since_seconds=args.since_seconds)
    if args.command == "lifecycle":
        return HostLifecycleWorker(paths, registry).run(args.action, args.instance_id)
    raise AssertionError(args.command)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        paths = ManagerPaths.for_current_user(registry_override=args.registry_dir)
        registry = InstanceRegistry(paths.registry_dir)
        if args.area == "host":
            payload = _run_host(args, paths)
        elif args.area == "access":
            payload = _run_access(args, paths)
        elif args.area == "codex":
            payload = _run_codex(args, paths)
        elif args.area == "cleanup":
            payload = _run_cleanup(args, paths)
        elif args.area == "_runtime":
            payload = _run_runtime(args, registry, paths)
            if payload is None:
                return 0
            # JSON-producing _runtime commands are an internal transport over
            # the transient user-systemd host boundary. A successfully
            # serialized semantic result (including ok=false) is transport
            # success; the outer public CLI maps ok=false to exit 1.
            _emit(payload, pretty=getattr(args, "pretty", False))
            return 0
        else:
            payload = _run_instance(args, registry, paths)
        _emit(payload, pretty=getattr(args, "pretty", False))
        return _payload_exit_code(payload)
    except ManagerError as exc:
        runtime_json_transport = (
            getattr(args, "area", None) == "_runtime"
            and getattr(args, "command", None) not in {"tunnel", "admission-guard", "codex-worker"}
        )
        if runtime_json_transport:
            # ManagerError from a JSON worker is a semantic result, not a
            # process/transport failure. Preserve the structured code/details
            # for HostExecutionBridge. Service runtimes keep nonzero exits.
            _emit(exc.to_dict(), pretty=getattr(args, "pretty", False))
            return 0
        _emit(exc.to_dict(), pretty=getattr(args, "pretty", False), stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
