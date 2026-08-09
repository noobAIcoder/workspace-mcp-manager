from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .domain import DesiredInstance
from .errors import ErrorCode, ManagerError
from .host import HostInspector
from .paths import ManagerPaths
from .redaction import redact_object
from .registry import InstanceRegistry


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
    return parser


def _run_host(args: argparse.Namespace, paths: ManagerPaths) -> dict[str, Any]:
    inspector = HostInspector(paths)
    if args.command == "inspect":
        return inspector.inspect()
    if args.command == "doctor":
        return inspector.doctor()
    if args.command == "components":
        return inspector.components()
    raise AssertionError(args.command)


def _run_instance(args: argparse.Namespace, registry: InstanceRegistry) -> dict[str, Any]:
    if args.command in {"validate", "create", "update"}:
        desired = _load_declaration(args.file)
        if args.command == "validate":
            return {
                "ok": True,
                "instance_id": desired.instance_id.value,
                "fingerprint": desired.fingerprint(),
                "desired": desired.to_dict(),
            }
        path = registry.create(desired) if args.command == "create" else registry.update(desired)
        return {
            "ok": True,
            "action": args.command,
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
    raise AssertionError(args.command)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        paths = ManagerPaths.for_current_user(registry_override=args.registry_dir)
        if args.area == "host":
            payload = _run_host(args, paths)
        else:
            payload = _run_instance(args, InstanceRegistry(paths.registry_dir))
        _emit(payload, pretty=args.pretty)
        return 0
    except ManagerError as exc:
        _emit(exc.to_dict(), pretty=getattr(args, "pretty", False), stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

