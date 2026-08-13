from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from workspace_mcp_manager.diagnostics import (
    HTTPResult,
    ResilienceDiagnosticService,
    _classify,
    _extract_control_plane_5xx,
    _is_poll_timeout,
    _memory_pressure,
    _parse_meminfo,
    _parse_psi,
)
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.paths import ManagerPaths

from tests.helpers import sample_instance


def paths_for(root: Path) -> ManagerPaths:
    return ManagerPaths(
        account_home=root,
        config_root=root / ".config/workspace-mcp-manager",
        registry_dir=root / ".config/workspace-mcp-manager/instances",
        state_root=root / ".local/state/workspace-mcp-manager",
        user_unit_dir=root / ".config/systemd/user",
        tunnel_profile_dir=root / ".config/workspace-mcp-manager/tunnel-profiles",
        manager_executable=root / ".local/bin/workspace-mcp-manager",
    )


def desired_for(root: Path) -> DesiredInstance:
    raw = sample_instance()
    raw["instance_id"] = "qual"
    raw["workspace_path"] = str(root / "workspace")
    raw["mcp"]["binary"] = "/usr/bin/true"
    raw["mcp"]["port"] = 17655
    raw["mcp"]["external_roots"] = []
    raw["tunnel"]["binary"] = "/usr/bin/true"
    raw["tunnel"]["id"] = "tunnel_qual123"
    raw["tunnel"]["profile"] = "workspace-mcp-qual"
    raw["tunnel"]["health_port"] = 17171
    raw["tunnel"]["env_file"] = str(root / "runtime.env")
    raw["access"] = {"read_only": [], "read_write": []}
    raw["github"] = {"config_dir": None}
    raw["recovery"]["admission_guard_enabled"] = False
    (root / "workspace").mkdir()
    return DesiredInstance.from_dict(raw)


class ResilienceDiagnosticTests(unittest.TestCase):
    def test_meminfo_and_psi_parsing(self) -> None:
        meminfo = _parse_meminfo("MemTotal:       1000 kB\nHugePages_Total: 4\n")
        self.assertEqual(meminfo["MemTotal"], {"value": 1000, "unit": "kB"})
        self.assertEqual(meminfo["HugePages_Total"], {"value": 4, "unit": None})
        psi = _parse_psi("some avg10=1.20 avg60=0.50 avg300=0.10 total=123\nfull avg10=0.00 total=2\n")
        self.assertEqual(psi["some"]["avg10"], 1.2)
        self.assertEqual(psi["some"]["total"], 123)
        self.assertEqual(psi["full"]["total"], 2)

    def test_tunnel_signature_helpers_are_control_plane_specific(self) -> None:
        five = {
            "component": "controlplane",
            "msg": "poll failed; backing off",
            "error": "server returned HTTP status 503",
        }
        self.assertEqual(_extract_control_plane_5xx(five), 503)
        self.assertIsNone(_extract_control_plane_5xx({"msg": "timestamp 2026-05-03"}))
        self.assertTrue(
            _is_poll_timeout(
                {
                    "component": "controlplane",
                    "msg": "poll timed out; backing off",
                    "error": "context deadline exceeded",
                }
            )
        )
        self.assertFalse(
            _is_poll_timeout(
                {"component": "dispatcher", "msg": "poll timed out; backing off", "error": "timeout"}
            )
        )

    def test_classifications_remain_in_independent_incident_domains(self) -> None:
        probes = {
            "mcp": {"discovery": {"status": 200}, "initialize": {"status": 503}},
            "tunnel": {},
        }
        services = {
            "mcp": {
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": 10,
                "ActiveEnterTimestampMonotonic": 100_000_000,
            },
            "tunnel": {},
        }
        events = [
            {
                "component": "controlplane",
                "control_plane_5xx": True,
                "poll_timeout": True,
            }
        ]
        labels, domains = _classify(
            probes=probes,
            services=services,
            cgroups={"mcp": {}, "tunnel": {}},
            tunnel_events=events,
            since_seconds=30,
            host_uptime_seconds=1000,
        )
        self.assertIn("local_mcp_5xx", labels)
        self.assertIn("tunnel_control_plane_5xx", labels)
        self.assertIn("tunnel_poll_timeout", labels)
        self.assertEqual(domains["local_mcp"], ["local_mcp_5xx"])
        self.assertEqual(
            domains["tunnel_control_plane"],
            ["tunnel_control_plane_5xx", "tunnel_poll_timeout"],
        )

    def test_recent_restart_and_memory_pressure_classification(self) -> None:
        labels, _domains = _classify(
            probes={"mcp": {"discovery": {"status": 200}, "initialize": {"status": 200}}},
            services={
                "mcp": {
                    "ActiveState": "active",
                    "SubState": "running",
                    "MainPID": 1,
                    "ActiveEnterTimestampMonotonic": 990_000_000,
                }
            },
            cgroups={
                "mcp": {"memory_events": {"oom_kill": 1}},
                "tunnel": {"memory_events": {}},
            },
            tunnel_events=[],
            since_seconds=30,
            host_uptime_seconds=1000,
        )
        self.assertIn("mcp_process_down_or_restart", labels)
        self.assertIn("memory_pressure", labels)
        self.assertTrue(_memory_pressure({"mcp": {"memory_current": 95, "memory_max": 100}}))
        self.assertFalse(_memory_pressure({"mcp": {"memory_current": 94, "memory_max": 100}}))

    def test_time_aware_tunnel_events_exclude_stale_and_future_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
            log = paths_for(root).state_root / "instances/qual/tunnel.log"
            log.parent.mkdir(parents=True)
            rows = [
                {
                    "time": (now - timedelta(hours=2)).isoformat(),
                    "level": "WARN",
                    "component": "controlplane",
                    "msg": "poll timed out; backing off",
                    "error": "context deadline exceeded",
                },
                {
                    "time": (now - timedelta(seconds=10)).isoformat(),
                    "level": "WARN",
                    "component": "controlplane",
                    "msg": "poll timed out; backing off",
                    "error": "Client.Timeout exceeded",
                },
                {
                    "time": (now - timedelta(seconds=5)).isoformat(),
                    "level": "ERROR",
                    "component": "controlplane",
                    "msg": "poll request failed",
                    "error": "server returned HTTP status 503",
                },
                {
                    "time": (now + timedelta(minutes=2)).isoformat(),
                    "level": "WARN",
                    "component": "controlplane",
                    "msg": "poll timed out; backing off",
                },
            ]
            log.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot-json\n", encoding="utf-8")
            service = ResilienceDiagnosticService(paths_for(root), now=lambda: now)
            events = service._recent_tunnel_events(instance_id="qual", captured_at=now, since_seconds=60)
            self.assertEqual(len(events), 2)
            self.assertTrue(any(event["poll_timeout"] for event in events))
            five = next(event for event in events if event["control_plane_5xx"])
            self.assertEqual(five["http_status"], 503)

    def test_cgroup_process_snapshot_captures_rss_pss_age_and_membership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            cgroups = root / "cgroup"
            pid = 321
            pid_root = proc / str(pid)
            pid_root.mkdir(parents=True)
            (proc / "uptime").write_text("1000.00 0.00\n", encoding="utf-8")
            (pid_root / "status").write_text("Name:\ttest\nVmRSS:\t1234 kB\n", encoding="utf-8")
            (pid_root / "smaps_rollup").write_text("Pss:                567 kB\n", encoding="utf-8")
            (pid_root / "cgroup").write_text("0::/test.slice/unit.service\n", encoding="utf-8")
            ticks = int(os.sysconf("SC_CLK_TCK"))
            tail = ["S", *(["0"] * 18), str(500 * ticks), "0"]
            (pid_root / "stat").write_text(f"{pid} (test) " + " ".join(tail) + "\n", encoding="utf-8")

            cg = cgroups / "test.slice/unit.service"
            cg.mkdir(parents=True)
            (cg / "cgroup.procs").write_text(f"{pid}\n", encoding="utf-8")
            (cg / "memory.current").write_text("1000\n", encoding="utf-8")
            (cg / "memory.peak").write_text("2000\n", encoding="utf-8")
            (cg / "memory.max").write_text("max\n", encoding="utf-8")
            (cg / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n", encoding="utf-8")
            (cg / "memory.events.local").write_text("low 0\nhigh 0\n", encoding="utf-8")

            service = ResilienceDiagnosticService(paths_for(root), proc_root=proc, cgroup_root=cgroups)
            snapshot = service._cgroup_snapshot("/test.slice/unit.service", host_uptime=1000.0)
            self.assertEqual(snapshot["memory_current"], 1000)
            process = snapshot["processes"][0]
            self.assertEqual(process["rss_kb"], 1234)
            self.assertEqual(process["pss_kb"], 567)
            self.assertAlmostEqual(process["age_seconds"], 500.0, places=1)
            self.assertIn("unit.service", process["cgroup"])

    def test_mcp_initialize_probe_deletes_created_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            service = ResilienceDiagnosticService(paths_for(root))
            discovery = HTTPResult(
                200,
                b'{"protocolVersion":"2025-11-25"}',
                {},
                None,
            )
            initialize = HTTPResult(
                200,
                b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-11-25"}}',
                {"Mcp-Session-Id": "session-1"},
                None,
            )
            cleanup = HTTPResult(200, b"", {}, None)
            with patch.object(service, "_http", side_effect=[discovery, initialize, cleanup]) as http:
                result = service._mcp_probes(desired)
            self.assertTrue(result["initialize"]["session_created"])
            self.assertTrue(result["initialize"]["session_cleanup"]["ok"])
            self.assertEqual(http.call_args_list[2].args[0], "DELETE")
            self.assertEqual(http.call_args_list[2].kwargs["headers"]["Mcp-Session-Id"], "session-1")

    def test_snapshot_is_persisted_and_recursively_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = desired_for(root)
            service = ResilienceDiagnosticService(
                paths_for(root),
                now=lambda: datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc),
            )
            host = {
                "uptime_seconds": 1000.0,
                "meminfo": {"available": True, "values": {}, "error": "CONTROL_PLANE_API_KEY=secret-value"},
                "psi": {},
            }
            service_state = {
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": 123,
                "ControlGroup": "/unit",
                "ActiveEnterTimestampMonotonic": 100_000_000,
            }
            with patch.object(service, "_host_evidence", return_value=host), \
                 patch.object(service, "_systemd_show", return_value=service_state), \
                 patch.object(service, "_cgroup_snapshot", return_value={"path": "/unit", "memory_events": {}}), \
                 patch.object(
                     service,
                     "_mcp_probes",
                     return_value={"discovery": {"status": 200}, "initialize": {"status": 200}},
                 ), \
                 patch.object(
                     service,
                     "_tunnel_probes",
                     return_value={"health": {"status": 200, "text": "live"}, "readiness": {"status": 200, "text": "ready"}},
                 ), \
                 patch.object(
                     service,
                     "_recent_tunnel_events",
                     return_value=[
                         {
                             "component": "controlplane",
                             "msg": "failure",
                             "error": "Bearer abc123",
                             "control_plane_5xx": True,
                             "poll_timeout": False,
                         }
                     ],
                 ):
                result = service.run(desired, since_seconds=60)
            path = Path(result["snapshot_path"])
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-value", persisted)
            self.assertNotIn("abc123", persisted)
            self.assertIn("<REDACTED>", persisted)
            self.assertIn("tunnel_control_plane_5xx", result["snapshot"]["classifications"])


if __name__ == "__main__":
    unittest.main()

