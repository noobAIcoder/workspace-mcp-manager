from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from workspace_mcp_manager.cli import build_parser
from workspace_mcp_manager.development_environment import (
    MANAGED_GITHUB_ENV_REMOVE,
    managed_github_subprocess_env,
)
from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.github_access import (
    ACCOUNT_ARGS,
    AUTH_STATUS_ARGS,
    BoundedCommandResult,
    GhResolution,
    GITHUB_ACCESS_PROJECTION_VERSION,
    GITHUB_ACCESS_VERIFICATION_RECORD_VERSION,
    GithubAccessService,
    GithubAccessBusy,
    SUPPORTED_GH_VERSIONS,
)
from workspace_mcp_manager.github_auth_helper import QUALIFIED_LOGIN_ARGS
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.setup_projection import PortProjectionService, SetupProjectionService
from workspace_mcp_manager.endpoint_projection import ListenerObservation, ListenerState

from tests.helpers import sample_v2_instance


SENTINEL = "github_pat_PM311_DO_NOT_LEAK_123456"


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


def managed_desired(root: Path, *, instance_id: str = "sample", deployment: str = "absent") -> DesiredInstance:
    raw = sample_v2_instance()
    raw["instance_id"] = instance_id
    raw["workspace_path"] = str(root / "workspace")
    Path(raw["workspace_path"]).mkdir(parents=True, exist_ok=True)
    raw["lifecycle"]["deployment"] = deployment
    if deployment == "absent":
        raw["lifecycle"]["runtime"] = "stopped"
    raw["github"] = {
        "mode": "managed",
        "config_dir": str(root / ".config/workspace-mcp-manager/github" / instance_id),
        "binary": "/usr/bin/gh",
    }
    raw["git"] = {"identity": None, "remote": None}
    raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
    raw["access"] = {"read_only": [], "read_write": []}
    return DesiredInstance.from_dict(raw)


def fake_gh(path: Path, *, version: str = "2.45.0") -> None:
    path.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f"VERSION={version!r}\n"
        "case \"${1-}\" in\n"
        "  --version) printf 'gh version %s (fake)\\n' \"$VERSION\"; exit 0 ;;\n"
        "esac\n"
        "if [ \"$*\" = 'auth status --hostname github.com' ]; then exit 0; fi\n"
        "if [ \"$*\" = 'api --hostname github.com user --jq .login' ]; then printf 'pm311-test-user\\n'; exit 0; fi\n"
        "if [ \"$*\" = 'auth login --hostname github.com --with-token --insecure-storage' ]; then\n"
        "  IFS= read -r token\n"
        f"  [ \"$token\" = {SENTINEL!r} ] || exit 41\n"
        f"  case \"$*\" in *{SENTINEL}*) exit 42;; esac\n"
        f"  env | grep -F {SENTINEL!r} >/dev/null && exit 43 || true\n"
        "  [ -n \"${GH_CONFIG_DIR-}\" ] || exit 44\n"
        "  mkdir -p \"$GH_CONFIG_DIR\"\n"
        "  umask 077\n"
        "  : > \"$GH_CONFIG_DIR/hosts.yml\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 40\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


class ManagedEnvironmentTests(unittest.TestCase):
    def test_managed_environment_removes_all_ambient_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = managed_desired(root)
            source = {key: f"value-{key}" for key in MANAGED_GITHUB_ENV_REMOVE}
            source.update({"HOME": "/wrong", "PATH": "/wrong", "SSH_AUTH_SOCK": "/wrong.sock"})
            env = managed_github_subprocess_env(paths_for(root), desired, source=source)
            for key in MANAGED_GITHUB_ENV_REMOVE:
                self.assertNotIn(key, env)
            self.assertEqual(env["HOME"], str(root))
            self.assertEqual(env["PATH"], desired.mcp.exec_path)
            self.assertEqual(env["GH_CONFIG_DIR"], desired.github.config_dir)
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(env["GH_PROMPT_DISABLED"], "1")
            self.assertEqual(env["GH_NO_UPDATE_NOTIFIER"], "1")
            self.assertNotIn("SSH_AUTH_SOCK", env)

    def test_generated_managed_service_and_https_helper_neutralize_ambient_github(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desired = managed_desired(root)
            raw = desired.to_dict()
            raw["git"]["remote"] = {"name": "origin", "protocol": "https-gh"}
            desired = DesiredInstance.from_dict(raw)
            resources = {item.resource_id: item for item in ResourceGenerator(paths_for(root)).generate(desired).resources}
            mcp = resources["mcp-unit"].content or ""
            helper = resources["github-cli-helper"].content or ""
            self.assertIn("UnsetEnvironment=" + " ".join(MANAGED_GITHUB_ENV_REMOVE), mcp)
            self.assertIn("unset " + " ".join(MANAGED_GITHUB_ENV_REMOVE), helper)
            self.assertIn("GH_CONFIG_DIR=", helper)
            self.assertIn("GIT_TERMINAL_PROMPT=0", helper)
            self.assertIn("exec /usr/bin/gh auth git-credential \"$@\"", helper)
            self.assertNotIn("auth setup-git", helper)


class ProfileAuthorityTests(unittest.TestCase):
    def test_registered_profile_provisioning_is_idempotent_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            desired = managed_desired(root)
            registry.create(desired)
            service = GithubAccessService(paths, registry)
            first = service.ensure_managed_profile("sample")
            second = service.ensure_managed_profile("sample")
            profile = Path(desired.github.config_dir or "")
            self.assertEqual(first["state"], "ready")
            self.assertEqual(second["state"], "ready")
            self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((profile / ".owner").stat().st_mode), 0o600)
            self.assertEqual((profile / ".owner").read_text(), "workspace-mcp-manager-instance=sample\n")

    def test_unregistered_or_foreign_profile_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            service = GithubAccessService(paths, registry)
            with self.assertRaises(Exception):
                service.ensure_managed_profile("sample")

            desired = managed_desired(root)
            registry.create(desired)
            profile = Path(desired.github.config_dir or "")
            profile.mkdir(parents=True, mode=0o700)
            (profile / ".owner").write_text("workspace-mcp-manager-instance=other\n", encoding="utf-8")
            (profile / ".owner").chmod(0o600)
            with self.assertRaises(Exception):
                service.ensure_managed_profile("sample")

    def test_symlinked_profile_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            desired = managed_desired(root)
            registry.create(desired)
            target = root / "foreign"
            target.mkdir()
            profile = Path(desired.github.config_dir or "")
            profile.parent.mkdir(parents=True)
            profile.symlink_to(target, target_is_directory=True)
            service = GithubAccessService(paths, registry)
            with self.assertRaises(Exception):
                service.ensure_managed_profile("sample")


class ProjectionAndVerificationTests(unittest.TestCase):
    def _service_with_fake_gh(self, root: Path, *, version: str = "2.45.0") -> tuple[GithubAccessService, DesiredInstance]:
        paths = paths_for(root)
        fake = root / "bin" / "gh"
        fake.parent.mkdir()
        fake_gh(fake, version=version)
        desired = managed_desired(root)
        raw = desired.to_dict()
        raw["github"]["binary"] = str(fake)
        desired = DesiredInstance.from_dict(raw)
        registry = InstanceRegistry(paths.registry_dir)
        registry.create(desired)
        service = GithubAccessService(paths, registry)
        service.ensure_managed_profile("sample")
        return service, desired

    def test_status_is_network_free_and_contract_is_multidimensional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _ = self._service_with_fake_gh(root)
            with mock.patch("workspace_mcp_manager.github_access.urllib.request.urlopen", side_effect=AssertionError("network")):
                payload = service.status("sample")
            self.assertEqual(payload["github_access_projection_version"], GITHUB_ACCESS_PROJECTION_VERSION)
            self.assertEqual(payload["profile"]["state"], "ready")
            self.assertEqual(payload["github_cli"]["version"], "2.45.0")
            self.assertTrue(payload["github_cli"]["supported"])
            self.assertEqual(payload["authentication"]["state"], "unknown")
            self.assertEqual(payload["verification"]["freshness"], "never")
            self.assertEqual(payload["git_transport"]["status"], "not_configured")

    def test_unsupported_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _ = self._service_with_fake_gh(Path(directory), version="2.46.0")
            payload = service.verify("sample")
            self.assertEqual(payload["verification"]["reason_code"], "GH_VERSION_UNSUPPORTED")
            self.assertFalse(payload["github_cli"]["supported"])

    def test_verification_record_is_secret_free_and_context_change_invalidates_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, desired = self._service_with_fake_gh(root)
            profile = Path(desired.github.config_dir or "")
            (profile / "hosts.yml").write_text("synthetic-non-secret-fixture\n", encoding="utf-8")
            (profile / "hosts.yml").chmod(0o600)
            payload = service.verify("sample")
            self.assertEqual(payload["authentication"]["state"], "authenticated")
            self.assertEqual(payload["authentication"]["account"], "pm311-test-user")
            self.assertEqual(payload["verification"]["manager_environment"], "passed")
            self.assertEqual(payload["verification"]["mcp_execution"], "not_deployed")
            record_path = paths_for(root).state_root / "github-access/sample.json"
            record = record_path.read_text(encoding="utf-8")
            self.assertNotIn(SENTINEL, record)
            decoded = json.loads(record)
            self.assertEqual(decoded["github_access_verification_record_version"], GITHUB_ACCESS_VERIFICATION_RECORD_VERSION)
            self.assertNotIn("stdout", decoded)
            self.assertNotIn("stderr", decoded)

            profile.rename(profile.with_name("sample-gone"))
            stale = service.status("sample")
            self.assertEqual(stale["verification"]["freshness"], "stale")
            self.assertFalse(stale["verification"]["context_valid"])
            self.assertEqual(stale["verification"]["reason_code"], "VERIFICATION_CONTEXT_CHANGED")

    def test_authenticated_profile_without_qualified_storage_fails_storage_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _ = self._service_with_fake_gh(root)
            payload = service.verify("sample")
            self.assertEqual(payload["authentication"]["state"], "authenticated")
            self.assertEqual(payload["verification"]["profile"], "failed")
            self.assertEqual(payload["verification"]["reason_code"], "PROFILE_MISSING")

    def test_unsafe_existing_hosts_storage_fails_before_auth_or_api_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, desired = self._service_with_fake_gh(root)
            profile = Path(desired.github.config_dir or "")
            foreign = root / "foreign-hosts.yml"
            foreign.write_text("fixture\n", encoding="utf-8")
            (profile / "hosts.yml").symlink_to(foreign)
            resolution = GhResolution(
                binary=str(root / "bin/gh"),
                version="2.45.0",
                raw_version="gh version 2.45.0 (fake)",
                available=True,
                supported=True,
            )
            with mock.patch.object(service, "_resolve_gh", return_value=resolution), \
                 mock.patch("workspace_mcp_manager.github_access._run_bounded", side_effect=AssertionError("auth/api execution")):
                payload = service.verify("sample")
            self.assertEqual(payload["authentication"]["state"], "unknown")
            self.assertEqual(payload["verification"]["profile"], "failed")
            self.assertEqual(payload["verification"]["reason_code"], "PROFILE_STORAGE_UNSAFE")

    def test_configure_launches_helper_without_credential_surface_and_reobserves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, desired = self._service_with_fake_gh(root)
            profile = Path(desired.github.config_dir or "")

            def helper_run(argv, **kwargs):
                self.assertNotIn(SENTINEL, " ".join(str(item) for item in argv))
                self.assertNotIn(SENTINEL, json.dumps(kwargs.get("env", {}), sort_keys=True))
                self.assertFalse(kwargs.get("start_new_session"))
                self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)
                self.assertIs(kwargs.get("stdout"), subprocess.DEVNULL)
                self.assertIs(kwargs.get("stderr"), subprocess.DEVNULL)
                (profile / "hosts.yml").write_text("fixture\n", encoding="utf-8")
                (profile / "hosts.yml").chmod(0o600)
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch("workspace_mcp_manager.github_access.subprocess.run", side_effect=helper_run) as launched:
                payload = service.configure("sample")
            self.assertEqual(launched.call_count, 1)
            self.assertEqual(payload["operation"]["outcome"], "succeeded")
            self.assertEqual(payload["status"]["authentication"]["state"], "authenticated")
            self.assertEqual(payload["status"]["verification"]["profile"], "passed")

    def test_external_verify_is_unavailable_without_provider_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            external = root / "external-gh"
            external.mkdir()
            raw = managed_desired(root).to_dict()
            raw["github"] = {"mode": "external", "config_dir": str(external), "binary": "/usr/bin/gh"}
            desired = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            service = GithubAccessService(paths, registry)
            with mock.patch("workspace_mcp_manager.github_access._run_bounded") as run:
                run.return_value.returncode = 0
                # Version resolution is the only subprocess allowed; no auth/api call.
                payload = service.verify("sample")
                invocations = [tuple(call.args[0]) for call in run.call_args_list]
            self.assertTrue(all("auth" not in args and "api" not in args for args in invocations))
            self.assertEqual(payload["verification"]["reason_code"], "EXTERNAL_PROFILE_WRITE_PROTECTION_UNAVAILABLE")

    def test_operation_lock_rejects_competing_operation_with_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            service, _ = self._service_with_fake_gh(root)
            lock_root = paths.state_root / "github-access/.locks"
            lock_root.mkdir(parents=True, exist_ok=True)
            lock_path = lock_root / "sample.lock"
            with lock_path.open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with mock.patch("workspace_mcp_manager.github_access.LOCK_TIMEOUT_SECONDS", 0.05):
                    with self.assertRaises(GithubAccessBusy):
                        with service._operation_lock("sample"):
                            self.fail("lock should not be acquired")

    def test_mcp_verification_uses_pinned_exec_command_shape_and_matches_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            desired = managed_desired(root, deployment="present")
            raw = desired.to_dict()
            raw["lifecycle"]["runtime"] = "running"
            desired = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            service = GithubAccessService(paths, registry)
            observed: list[dict[str, object]] = []

            def mcp_reply(_url, payload, **kwargs):
                observed.append({"payload": payload, "session_id": kwargs.get("session_id")})
                method = payload.get("method")
                if method == "initialize":
                    return (
                        {
                            "result": {
                                "protocolVersion": "2025-11-25",
                                "serverInfo": {"name": "coding-tools-mcp", "version": "0.2.2"},
                            }
                        },
                        "session-pm311",
                    )
                if method == "notifications/initialized":
                    return ({}, None)
                if method == "tools/call":
                    return (
                        {
                            "result": {
                                "isError": False,
                                "structuredContent": {
                                    "ok": True,
                                    "exit_code": 0,
                                    "truncated": False,
                                    "stdout": "pm311-test-user\n",
                                },
                            }
                        },
                        None,
                    )
                raise AssertionError(method)

            active = BoundedCommandResult(
                argv=("systemctl",),
                returncode=0,
                stdout=b"active\n",
                stderr=b"",
                timed_out=False,
                output_overflow=False,
            )
            with mock.patch("workspace_mcp_manager.github_access.shutil.which", return_value="/usr/bin/systemctl"), \
                 mock.patch("workspace_mcp_manager.github_access._run_bounded", return_value=active), \
                 mock.patch.object(service, "_http_json", side_effect=mcp_reply), \
                 mock.patch("workspace_mcp_manager.github_access.urllib.request.urlopen", side_effect=OSError("delete unavailable")):
                result, reason = service._verify_mcp(desired, "/usr/bin/gh", "pm311-test-user")
            self.assertEqual((result, reason), ("passed", None))
            call = observed[2]["payload"]
            self.assertEqual(call["method"], "tools/call")
            arguments = call["params"]["arguments"]
            self.assertEqual(arguments["cmd"], "/usr/bin/gh api --hostname github.com user --jq .login")
            self.assertEqual(arguments["timeout_ms"], 8000)
            self.assertEqual(arguments["max_output_bytes"], 8192)
            self.assertEqual(arguments["preview_bytes"], 4096)
            self.assertEqual(arguments["verbosity"], "full")


class CandidateAuthorityTests(unittest.TestCase):
    def test_frontend_managed_intent_derives_profile_and_binary(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            registry = InstanceRegistry(paths.registry_dir)
            ports = PortProjectionService(registry, listener_observation=ListenerObservation(ListenerState.AVAILABLE, ()))
            service = SetupProjectionService(paths, registry, ports=ports)
            fake = root / "bin" / "gh"
            fake.parent.mkdir()
            fake_gh(fake)
            request = {
                "candidate_request_version": 1,
                "workspace_path": str(workspace),
                "field_edits": [
                    {"path": "/instance_id", "operation": "set", "value": "managed-test"},
                    {"path": "/mcp/exec_path", "operation": "set", "value": f"{fake.parent}:/usr/bin:/bin"},
                    {"path": "/github/mode", "operation": "set", "value": "managed"},
                    {"path": "/tunnel/id", "operation": "set", "value": "tunnel_managedtest"},
                ],
                "access_edits": [],
            }
            payload = service.candidate(request)
            github = payload["effective_declaration"]["github"]
            self.assertEqual(github["mode"], "managed")
            self.assertEqual(github["config_dir"], str(paths.config_root / "github/managed-test"))
            self.assertEqual(github["binary"], str(fake.resolve()))
            provenance = {item["path"]: item for item in payload["field_provenance"]}
            self.assertEqual(provenance["/github/config_dir"]["source"], "manager_derived")
            self.assertEqual(provenance["/github/binary"]["source"], "manager_derived")

            bad = dict(request)
            bad["field_edits"] = list(request["field_edits"]) + [
                {"path": "/github/config_dir", "operation": "set", "value": str(root / "frontend")}
            ]
            with self.assertRaises(Exception):
                service.candidate(bad)

    def test_existing_disabled_instance_can_enable_managed_without_git_transport_mutation(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "remote", "add", "origin", "git@github.com:example/project.git"],
                check=True,
            )
            raw = sample_v2_instance()
            raw["instance_id"] = "existing"
            raw["workspace_path"] = str(workspace)
            raw["github"] = {"mode": "disabled", "config_dir": None, "binary": None}
            raw["git"]["remote"] = {"name": "origin", "protocol": "ssh"}
            raw["agent"] = {"mode": "external", "ssh_auth_sock": "/tmp/pm311-agent.sock"}
            raw["access"] = {"read_only": [], "read_write": []}
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(DesiredInstance.from_dict(raw))
            ports = PortProjectionService(registry, listener_observation=ListenerObservation(ListenerState.AVAILABLE, ()))
            service = SetupProjectionService(paths, registry, ports=ports)
            fake = root / "bin" / "gh"
            fake.parent.mkdir()
            fake_gh(fake)
            request = {
                "candidate_request_version": 1,
                "workspace_path": str(workspace),
                "field_edits": [
                    {"path": "/mcp/exec_path", "operation": "set", "value": f"{fake.parent}:/usr/bin:/bin"},
                    {"path": "/github/mode", "operation": "set", "value": "managed"},
                ],
                "access_edits": [],
            }
            before_remote = subprocess.check_output(
                ["git", "-C", str(workspace), "config", "--local", "--get", "remote.origin.url"], text=True
            ).strip()
            payload = service.candidate(request)
            desired = payload["effective_declaration"]
            self.assertEqual(desired["instance_id"], "existing")
            self.assertEqual(desired["github"]["mode"], "managed")
            self.assertEqual(desired["github"]["config_dir"], str(paths.config_root / "github/existing"))
            self.assertEqual(desired["git"]["remote"]["protocol"], "ssh")
            self.assertEqual(
                subprocess.check_output(
                    ["git", "-C", str(workspace), "config", "--local", "--get", "remote.origin.url"], text=True
                ).strip(),
                before_remote,
            )

    def test_existing_external_profile_cannot_be_migrated_to_managed(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            raw = sample_v2_instance()
            raw["instance_id"] = "external-existing"
            raw["workspace_path"] = str(workspace)
            raw["github"] = {"mode": "external", "config_dir": str(root / "external-gh"), "binary": "/usr/bin/gh"}
            raw["git"] = {"identity": None, "remote": None}
            raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
            raw["access"] = {"read_only": [], "read_write": []}
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(DesiredInstance.from_dict(raw))
            ports = PortProjectionService(registry, listener_observation=ListenerObservation(ListenerState.AVAILABLE, ()))
            service = SetupProjectionService(paths, registry, ports=ports)
            with self.assertRaises(Exception):
                service.candidate(
                    {
                        "candidate_request_version": 1,
                        "workspace_path": str(workspace),
                        "field_edits": [{"path": "/github/mode", "operation": "set", "value": "managed"}],
                        "access_edits": [],
                    }
                )


class VisibleCredentialHelperTests(unittest.TestCase):
    @staticmethod
    def _read_pty(fd: int) -> bytes:
        """Treat Linux PTY master EIO after slave closure as end-of-file."""

        try:
            return os.read(fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return b""
            raise

    @unittest.skipUnless(hasattr(pty, "fork"), "PTY support unavailable")
    def test_visible_pty_input_reaches_fake_gh_only_through_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            fake = root / "gh"
            fake_gh(fake)
            try:
                child, fd = pty.fork()
            except OSError as exc:
                self.skipTest(f"PTY allocation unavailable: {exc}")
            if child == 0:
                os.execvpe(
                    sys.executable,
                    [
                        sys.executable,
                        "-m",
                        "workspace_mcp_manager.github_auth_helper",
                        "--instance-id",
                        "sample",
                        "--profile",
                        str(profile),
                        "--gh-binary",
                        str(fake),
                        "--home",
                        str(root),
                        "--exec-path",
                        f"{root}:/usr/bin:/bin",
                        "--timeout-seconds",
                        "5",
                    ],
                    {**os.environ, "GH_TOKEN": SENTINEL, "GITHUB_TOKEN": SENTINEL},
                )
                raise AssertionError("exec returned")

            transcript = bytearray()
            try:
                deadline = time.monotonic() + 5
                while b"Token:\r\n> " not in transcript and time.monotonic() < deadline:
                    chunk = self._read_pty(fd)
                    if not chunk:
                        break
                    transcript.extend(chunk)
                self.assertIn(b"Input is VISIBLE", transcript)
                os.write(fd, SENTINEL.encode() + b"\n")
                while time.monotonic() < deadline:
                    chunk = self._read_pty(fd)
                    if not chunk:
                        break
                    transcript.extend(chunk)
                _, status = os.waitpid(child, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                text = transcript.decode("utf-8", errors="replace")
                # One occurrence is the terminal line discipline echo. The helper
                # never prints the credential independently.
                self.assertEqual(text.count(SENTINEL), 1)
                self.assertTrue((profile / "hosts.yml").is_file())
                self.assertEqual(stat.S_IMODE((profile / "hosts.yml").stat().st_mode), 0o600)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass


class ContractConstantTests(unittest.TestCase):
    def test_exact_qualified_vectors_and_version_table(self) -> None:
        self.assertEqual(SUPPORTED_GH_VERSIONS, frozenset({"2.45.0"}))
        self.assertEqual(AUTH_STATUS_ARGS, ("auth", "status", "--hostname", "github.com"))
        self.assertEqual(ACCOUNT_ARGS, ("api", "--hostname", "github.com", "user", "--jq", ".login"))
        self.assertEqual(
            QUALIFIED_LOGIN_ARGS,
            ("auth", "login", "--hostname", "github.com", "--with-token", "--insecure-storage"),
        )
        self.assertNotIn("--git-protocol", QUALIFIED_LOGIN_ARGS)

    def test_public_cli_has_task_surface_and_no_credential_argument(self) -> None:
        parser = build_parser()
        for action in ("status", "verify", "configure"):
            parsed = parser.parse_args(["instance", "github-access", action, "sample"])
            self.assertEqual(parsed.command, "github-access")
            self.assertEqual(parsed.github_access_action, action)
            self.assertFalse(hasattr(parsed, "token"))
            self.assertFalse(hasattr(parsed, "credential"))
            self.assertFalse(hasattr(parsed, "token_file"))


if __name__ == "__main__":
    unittest.main()
