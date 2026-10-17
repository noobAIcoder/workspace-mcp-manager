"""Recognized workspace-mcp 0.3.2 resource templates.

Copied from the installed predecessor's renderers for strict data-only comparison.
No legacy program is imported or executed during discovery or migration.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import shutil
from .errors import config_error

ADMISSION_GUARD_INTERVAL_SECONDS = 30

def systemd_quote(value: str) -> str:
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

def systemd_path(value: str) -> str:
    if not value.startswith("/"):
        raise config_error(f"systemd path must be absolute: {value}")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise config_error(f"systemd path contains whitespace or control characters: {value}")
    if any(character in {'"', "'", "\\"} for character in value):
        raise config_error(f"systemd path contains unsupported quoting characters: {value}")
    return value.replace("%", "%%")

def yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)

class LegacyTemplates:
    def __init__(self, paths, values, *, script_path=None):
        self.config = SimpleNamespace(**{k.lower(): v for k,v in values.items()})
        self.config.path = paths.account_home / '.config/workspace-mcp' / (values['INSTANCE_ID'] + '.conf')
        self.home = paths.account_home
        self.script_path = script_path or self.home / '.local/lib/workspace-mcp/workspace-mcp'
        self.state_dir = self.home / '.local/state/workspace-mcp' / self.config.instance_id
        self.mcp_unit_name = 'coding-tools-mcp-' + self.config.instance_id + '.service'
        self.tunnel_unit_name = 'tunnel-client-' + self.config.instance_id + '.service'
        self.admission_guard_unit_name = 'workspace-mcp-admission-guard-' + self.config.instance_id + '.service'
        self.admission_guard_timer_name = 'workspace-mcp-admission-guard-' + self.config.instance_id + '.timer'
        self.mcp_log = self.state_dir / 'mcp.log'
        self.tunnel_supervisor_log = self.state_dir / 'tunnel-supervisor.log'
        self.admission_guard_log = self.state_dir / 'admission-guard.log'
        self.mcp_url = f'http://{self.config.mcp_host}:{self.config.mcp_port}/mcp'
        self.paths = paths

    def tool(self, name):
        return getattr(self, 'curl_binary', None) or shutil.which(name) or '/usr/bin/' + name

    @property
    def discovery_url(self):
        return f'http://{self.config.mcp_host}:{self.config.mcp_port}/.well-known/mcp.json'

    def files(self):
        return {
            self.paths.user_unit_dir / self.mcp_unit_name: self.render_mcp_unit(),
            self.paths.user_unit_dir / self.tunnel_unit_name: self.render_tunnel_unit(),
            self.paths.user_unit_dir / self.admission_guard_unit_name: self.render_admission_guard_unit(),
            self.paths.user_unit_dir / self.admission_guard_timer_name: self.render_admission_guard_timer(),
            self.home / '.config/tunnel-client' / (self.config.tunnel_profile + '.yaml'): self.render_profile(),
        }

    def render_profile(self) -> str:
        return f"""# workspace-mcp-instance={self.config.instance_id}
config_version: 1
control_plane:
  base_url: \"https://api.openai.com\"
  tunnel_id: {yaml_string(self.config.tunnel_id)}
  api_key: \"env:CONTROL_PLANE_API_KEY\"
health:
  listen_addr: {yaml_string(f'{self.config.tunnel_health_host}:{self.config.tunnel_health_port}')}
admin_ui:
  open_browser: false
log:
  level: info
  format: json
mcp:
  server_urls:
    - channel: main
      url: {yaml_string(self.mcp_url)}
"""

    def render_mcp_unit(self) -> str:
        exec_start = " ".join(
            systemd_quote(value)
            for value in (
                str(self.config.mcp_binary),
                "--workspace",
                str(self.config.workspace_path),
                "--host",
                self.config.mcp_host,
                "--port",
                str(self.config.mcp_port),
                "--permission-mode",
                self.config.mcp_permission_mode,
                "--shell-env-inherit",
                self.config.mcp_shell_env_inherit,
            )
        )
        return f"""# workspace-mcp-instance={self.config.instance_id}
# workspace-mcp-port={self.config.mcp_port}
[Unit]
Description=Coding Tools MCP - {self.config.instance_id}
After=network.target

[Service]
Type=simple
WorkingDirectory={systemd_path(str(self.config.workspace_path))}
Environment=\"PYTHONUNBUFFERED=1\"
Environment={systemd_quote('PATH=' + self.config.mcp_exec_path)}
ExecStart={exec_start}
Restart=on-failure
RestartSec=2
TimeoutStopSec=10
KillMode=mixed
StandardOutput=append:{systemd_path(str(self.mcp_log))}
StandardError=append:{systemd_path(str(self.mcp_log))}

[Install]
WantedBy=default.target
"""

    def render_tunnel_unit(self) -> str:
        curl = self.tool("curl")
        exec_pre = " ".join(
            systemd_quote(value)
            for value in (
                curl,
                "--fail",
                "--silent",
                "--show-error",
                self.discovery_url,
            )
        )
        exec_start = " ".join(
            systemd_quote(value)
            for value in (
                str(self.script_path),
                "_run-tunnel",
            )
        )
        return f"""# workspace-mcp-instance={self.config.instance_id}
# workspace-mcp-profile={self.config.tunnel_profile}
# workspace-mcp-health-port={self.config.tunnel_health_port}
# workspace-mcp-tunnel-id={self.config.tunnel_id}
[Unit]
Description=OpenAI MCP Tunnel - {self.config.instance_id}
Requires={self.mcp_unit_name}
After={self.mcp_unit_name}

[Service]
Type=simple
WorkingDirectory={systemd_path(str(self.home))}
UMask=0077
Environment={systemd_quote('WORKSPACE_MCP_CONFIG=' + str(self.config.path))}
ExecStartPre={exec_pre}
ExecStart={exec_start}
Restart=always
RestartSec=5
TimeoutStopSec=15
KillMode=mixed
StandardOutput=append:{systemd_path(str(self.tunnel_supervisor_log))}
StandardError=append:{systemd_path(str(self.tunnel_supervisor_log))}

[Install]
WantedBy=default.target
"""

    def render_admission_guard_unit(self) -> str:
        exec_start = " ".join(
            systemd_quote(value)
            for value in (
                str(self.script_path),
                "_admission-guard",
            )
        )
        return f"""# workspace-mcp-instance={self.config.instance_id}
[Unit]
Description=Workspace MCP admission recovery guard - {self.config.instance_id}
After={self.mcp_unit_name}

[Service]
Type=oneshot
Environment={systemd_quote('WORKSPACE_MCP_CONFIG=' + str(self.config.path))}
ExecStart={exec_start}
StandardOutput=append:{systemd_path(str(self.admission_guard_log))}
StandardError=append:{systemd_path(str(self.admission_guard_log))}
"""

    def render_admission_guard_timer(self) -> str:
        return f"""# workspace-mcp-instance={self.config.instance_id}
[Unit]
Description=Workspace MCP admission recovery timer - {self.config.instance_id}

[Timer]
OnBootSec={ADMISSION_GUARD_INTERVAL_SECONDS}s
OnUnitActiveSec={ADMISSION_GUARD_INTERVAL_SECONDS}s
AccuracySec=1s
Unit={self.admission_guard_unit_name}

[Install]
WantedBy=timers.target
"""
