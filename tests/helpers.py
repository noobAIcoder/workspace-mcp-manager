from __future__ import annotations

from copy import deepcopy


def sample_instance() -> dict:
    return deepcopy(
        {
            "config_version": 1,
            "instance_id": "sample",
            "workspace_path": "/srv/workspaces/sample",
            "lifecycle": {"deployment": "present", "runtime": "running"},
            "mcp": {
                "binary": "/opt/bin/coding-tools-mcp",
                "host": "127.0.0.1",
                "port": 8765,
                "permission_mode": "trusted",
                "shell_env_inherit": "all",
                "exec_path": "/opt/bin:/usr/bin:/bin",
                "external_roots": ["/opt/toolchains/codex"],
            },
            "tunnel": {
                "binary": "/opt/bin/tunnel-client",
                "id": "tunnel_abc123",
                "profile": "workspace-mcp-sample",
                "health_host": "127.0.0.1",
                "health_port": 8080,
                "env_file": "/home/operator/.config/tunnel-client/runtime.env",
            },
            "access": {
                "read_only": [{"alias": "docs", "path": "/srv/shared/docs"}],
                "read_write": [{"alias": "scratch_rw", "path": "/srv/shared/scratch"}],
            },
            "github": {"config_dir": "/home/operator/.config/gh-sample"},
            "recovery": {
                "admission_guard_enabled": True,
                "guard_interval_seconds": 30,
                "recovery_cooldown_seconds": 120,
            },
        }
    )


def sample_v2_instance() -> dict:
    value = sample_instance()
    value["config_version"] = 2
    value["github"] = {
        "mode": "managed",
        "config_dir": "/home/operator/.config/workspace-mcp-manager/github/sample",
        "binary": "/usr/bin/gh",
    }
    value["git"] = {
        "identity": {
            "name": "Example Operator",
            "email": "operator@example.invalid",
        },
        "remote": {
            "name": "origin",
            "protocol": "ssh",
        },
    }
    value["agent"] = {
        "mode": "managed-ssh-agent",
        "ssh_auth_sock": None,
    }
    return value

