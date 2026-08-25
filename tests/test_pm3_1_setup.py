from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.endpoint_projection import (
    Endpoint,
    ListenerObservation,
    ListenerState,
    endpoint_overlap,
)
from workspace_mcp_manager.errors import ErrorCode, ManagerError
from workspace_mcp_manager.operator_contracts import operator_template
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import (
    HostResourceObserver,
    ObservationStatus,
    PlanOperation,
    ReconciliationPlanner,
    ResourceObservation,
    UnitObservation,
)
from workspace_mcp_manager.registry import InstanceRegistry
from workspace_mcp_manager.setup_projection import (
    AUTHENTICATION_STATUS_VERSION,
    CANDIDATE_REQUEST_VERSION,
    CANDIDATE_VERSION,
    DISCOVERY_VERSION,
    PortProjectionService,
    SetupProjectionService,
    authentication_status,
    discover_workspace,
    normalize_instance_id,
    suggest_instance_id,
)
import workspace_mcp_manager.setup_projection as setup_projection

from tests.helpers import sample_v2_instance


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


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def init_repo(path: Path, *, local_identity: bool = False, remote: str | None = None) -> None:
    path.mkdir(parents=True)
    git("init", "-q", str(path))
    if local_identity:
        git("-C", str(path), "config", "--local", "user.name", "Example User")
        git("-C", str(path), "config", "--local", "user.email", "example@example.invalid")
    if remote is not None:
        git("-C", str(path), "remote", "add", "origin", remote)


def desired_for(instance_id: str, workspace: Path, *, mcp_port: int, health_port: int) -> DesiredInstance:
    raw = sample_v2_instance()
    raw["instance_id"] = instance_id
    raw["workspace_path"] = str(workspace)
    raw["mcp"]["port"] = mcp_port
    raw["tunnel"]["health_port"] = health_port
    raw["tunnel"]["id"] = f"tunnel_{instance_id.replace('-', '')}"
    raw["tunnel"]["profile"] = f"workspace-mcp-{instance_id}"
    raw["access"] = {"read_only": [], "read_write": []}
    raw["git"] = {"identity": None, "remote": None}
    raw["github"] = {"mode": "disabled", "config_dir": None, "binary": None}
    raw["agent"] = {"mode": "none", "ssh_auth_sock": None}
    return DesiredInstance.from_dict(raw)


class EndpointModelTests(unittest.TestCase):
    def test_bind_overlap_contract(self) -> None:
        exact = Endpoint("127.0.0.1", 7654, "MCP", "candidate")
        self.assertTrue(endpoint_overlap(exact, Endpoint("127.0.0.1", 7654, "MCP", "declared")))
        self.assertTrue(endpoint_overlap(exact, Endpoint("0.0.0.0", 7654, "External", "observed")))
        self.assertFalse(endpoint_overlap(exact, Endpoint("::1", 7654, "External", "observed")))
        self.assertTrue(
            endpoint_overlap(
                exact,
                Endpoint("::", 7654, "External", "observed", dual_stack_unknown=True),
            )
        )
        self.assertTrue(endpoint_overlap(exact, Endpoint("::ffff:127.0.0.1", 7654, "External", "observed")))
        self.assertIsNone(endpoint_overlap(Endpoint("localhost", 7654, "MCP", "candidate"), exact))
        self.assertFalse(endpoint_overlap(exact, Endpoint("127.0.0.1", 7655, "MCP", "declared")))

    def test_cross_purpose_declared_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InstanceRegistry(paths_for(root).registry_dir)
            registry.create(desired_for("declared", root / "declared", mcp_port=7654, health_port=7070))
            ports = PortProjectionService(
                registry,
                listener_observation=ListenerObservation(ListenerState.AVAILABLE, ()),
            )
            candidate = operator_template(paths_for(root))["defaults"]
            candidate["instance_id"] = "new"
            candidate["workspace_path"] = str(root / "new")
            candidate["mcp"]["port"] = 7999
            candidate["tunnel"]["health_port"] = 7654
            projection = ports.candidate_projection(candidate)
            self.assertEqual(projection["collision_state"], "conflict")
            self.assertTrue(projection["conflicts"])

    def test_unknown_listener_state_is_provisional_and_not_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InstanceRegistry(paths_for(root).registry_dir)
            ports = PortProjectionService(
                registry,
                listener_observation=ListenerObservation(ListenerState.UNAVAILABLE, (), "ss unavailable"),
            )
            recommendation = ports.recommend(purpose="MCP", host="127.0.0.1")
            self.assertEqual(recommendation["port"], 7654)
            self.assertEqual(recommendation["status"], "provisional")
            candidate = operator_template(paths_for(root))["defaults"]
            candidate["instance_id"] = "new"
            candidate["workspace_path"] = str(root / "new")
            candidate["mcp"]["port"] = 7654
            candidate["tunnel"]["health_port"] = 7070
            self.assertEqual(ports.candidate_projection(candidate)["collision_state"], "unknown")

    def test_unknown_listener_allows_unchanged_owned_assignment_but_not_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InstanceRegistry(paths_for(root).registry_dir)
            desired = desired_for("managed", root / "managed", mcp_port=7654, health_port=7070)
            registry.create(desired)
            ports = PortProjectionService(
                registry,
                listener_observation=ListenerObservation(ListenerState.UNAVAILABLE, (), "ss unavailable"),
            )
            self.assertEqual(ports.candidate_projection(desired.to_dict())["collision_state"], "clear")
            changed = desired.to_dict()
            changed["mcp"]["port"] = 7655
            self.assertEqual(ports.candidate_projection(changed)["collision_state"], "unknown")

    def test_planner_fails_closed_for_new_endpoint_when_listener_observation_is_unknown(self) -> None:
        class UnknownObserver(HostResourceObserver):
            def check_path(self, *args, **kwargs):  # type: ignore[no-untyped-def,override]
                return ResourceObservation(ObservationStatus.EXACT, "exact")

            def observe_resource(self, resource):  # type: ignore[no-untyped-def,override]
                return ResourceObservation(ObservationStatus.ABSENT, "absent")

            def observe_unit(self, unit_name: str) -> UnitObservation:
                return UnitObservation(False, False, False)

            def managed_identities(self) -> list[dict[str, str]]:
                return []

            def listener_observation(self) -> ListenerObservation:
                return ListenerObservation(ListenerState.UNAVAILABLE, (), "ss unavailable")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            desired = desired_for("new", root / "new", mcp_port=7654, health_port=7070)
            plan = ReconciliationPlanner(paths, registry, observer=UnknownObserver()).plan(desired)
            self.assertFalse(plan.valid)
            self.assertTrue(
                any(
                    item.operation is PlanOperation.CONFLICT and "fails closed" in item.reason
                    for item in plan.operations
                )
            )


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is required for PM3.1 discovery tests")

    def test_nested_repo_resolves_root_and_existing_symlink_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            repo = root / "repos/LeadBot"
            init_repo(repo)
            nested = repo / "packages/core"
            nested.mkdir(parents=True)
            registry.create(desired_for("leadbot", repo, mcp_port=7654, health_port=7070))
            alias = root / "leadbot-link"
            alias.symlink_to(repo, target_is_directory=True)

            payload = discover_workspace(paths, registry, str(alias / "packages/core"))
            self.assertEqual(payload["discovery_version"], DISCOVERY_VERSION)
            self.assertEqual(payload["workspace_path"], str(repo.resolve()))
            self.assertEqual(payload["workspace_identity"], str(repo.resolve()))
            self.assertTrue(payload["repository_detected"])
            self.assertEqual(payload["instance_match"]["status"], "single")
            self.assertEqual(payload["instance_id_suggestion"], "leadbot")
            self.assertEqual(len(payload["discovery_fingerprint"]), 64)

    def test_non_repo_and_conflicting_workspace_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            workspace = root / "plain"
            workspace.mkdir()
            registry.create(desired_for("plain-a", workspace, mcp_port=7654, health_port=7070))
            registry.create(desired_for("plain-b", workspace, mcp_port=7655, health_port=7071))
            payload = discover_workspace(paths, registry, str(workspace))
            self.assertFalse(payload["repository_detected"])
            self.assertEqual(payload["instance_match"]["status"], "conflict")

    def test_instance_id_normalization_and_collision_suffix(self) -> None:
        self.assertEqual(normalize_instance_id("LeadBot"), "leadbot")
        self.assertEqual(normalize_instance_id("my_project"), "my-project")
        self.assertEqual(normalize_instance_id("Ångström Δ"), "angstrom")
        self.assertEqual(suggest_instance_id("leadbot", {"leadbot", "leadbot-2"}), "leadbot-3")
        long_name = "x" * 80
        suffixed = suggest_instance_id(long_name, {"x" * 63})
        self.assertLessEqual(len(suffixed), 63)
        self.assertTrue(suffixed.endswith("-2"))

    def test_secret_bearing_remote_is_sanitized_and_discovery_never_ls_remote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            repo = root / "repo"
            init_repo(
                repo,
                local_identity=True,
                remote="https://oauth2:SUPER_SECRET_TOKEN@github.com/noobAIcoder/LeadBot.git",
            )
            calls: list[tuple[str, ...]] = []
            original = setup_projection._run_local

            def recording(argv, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(tuple(argv))
                return original(argv, **kwargs)

            with mock.patch.object(setup_projection, "_run_local", side_effect=recording):
                payload = discover_workspace(paths, registry, str(repo))
            serialized = json.dumps(payload, sort_keys=True)
            self.assertNotIn("SUPER_SECRET_TOKEN", serialized)
            remote = payload["local_git"]["remote"]
            self.assertEqual(remote["host"], "github.com")
            self.assertEqual(remote["repository"], "noobAIcoder/LeadBot")
            self.assertEqual(remote["desired_protocol"], "https-gh")
            self.assertTrue(remote["userinfo_present"])
            self.assertEqual(remote["classification"], "foreign")
            self.assertFalse(any("ls-remote" in call for call in calls))

    def test_inherited_identity_is_observed_but_not_adoptable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            repo = root / "repo"
            init_repo(repo)
            (root / ".gitconfig").write_text(
                "[user]\n\tname = Global User\n\temail = global@example.invalid\n",
                encoding="utf-8",
            )
            payload = discover_workspace(paths, registry, str(repo))
            identity = payload["local_git"]["identity"]
            self.assertEqual(identity["classification"], "observed_only")
            self.assertEqual(identity["effective"]["scope"], "inherited")


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is required for PM3.1 candidate tests")

    def _service(self, root: Path, registry: InstanceRegistry) -> SetupProjectionService:
        ports = PortProjectionService(
            registry,
            listener_observation=ListenerObservation(ListenerState.AVAILABLE, ()),
        )
        return SetupProjectionService(paths_for(root), registry, ports=ports)

    def test_template_v2_removes_tunnel_profile_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = operator_template(paths_for(Path(directory)))
        self.assertEqual(payload["template_version"], 3)
        self.assertEqual(payload["config_version"], 2)
        self.assertNotIn("tunnel.profile", payload["required_operator_fields"])

    def test_candidate_copy_precedence_target_git_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            registry = InstanceRegistry(paths.registry_dir)
            source_workspace = root / "source"
            source_workspace.mkdir()
            source_raw = desired_for("source", source_workspace, mcp_port=7900, health_port=7300).to_dict()
            source_raw["mcp"]["permission_mode"] = "safe"
            source_raw["access"] = {
                "read_only": [{"alias": "docs", "path": str(root / "shared-docs")}],
                "read_write": [],
            }
            source_raw["git"] = {
                "identity": {"name": "Source User", "email": "source@example.invalid"},
                "remote": None,
            }
            registry.create(DesiredInstance.from_dict(source_raw))

            target = root / "repos/Target"
            init_repo(target, local_identity=True)
            service = self._service(root, registry)
            request = {
                "candidate_request_version": CANDIDATE_REQUEST_VERSION,
                "workspace_path": str(target),
                "copy_source_instance_id": "source",
                "field_edits": [{"path": "/instance_id", "operation": "set", "value": "target-custom"}],
                "access_edits": [],
            }
            payload = service.candidate(request)
            desired = payload["effective_declaration"]
            self.assertEqual(payload["candidate_version"], CANDIDATE_VERSION)
            self.assertEqual(desired["instance_id"], "target-custom")
            self.assertEqual(desired["workspace_path"], str(target.resolve()))
            self.assertEqual(desired["mcp"]["permission_mode"], "safe")
            self.assertNotEqual(desired["mcp"]["port"], 7900)
            self.assertEqual(desired["tunnel"]["profile"], "workspace-mcp-target-custom")
            self.assertEqual(desired["git"]["identity"]["name"], "Example User")
            self.assertEqual(desired["access"]["read_only"][0]["alias"], "docs")
            self.assertIn("tunnel.id", payload["unresolved_required_operator_fields"])
            provenance = {item["path"]: item for item in payload["field_provenance"]}
            self.assertEqual(provenance["/mcp/permission_mode"]["source"], "copy_source")
            self.assertEqual(provenance["/mcp/port"]["source"], "manager_recommendation")
            self.assertEqual(provenance["/tunnel/profile"]["source"], "manager_derived")
            self.assertEqual(provenance["/git/identity/name"]["source"], "workspace_discovery")
            self.assertEqual(provenance["/instance_id"]["source"], "operator_override")

    def test_set_clear_illegal_edit_and_stable_input_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InstanceRegistry(paths_for(root).registry_dir)
            target = root / "repo"
            init_repo(target, local_identity=True)
            service = self._service(root, registry)
            request = {
                "candidate_request_version": 1,
                "workspace_path": str(target),
                "field_edits": [
                    {"path": "/mcp/permission_mode", "operation": "set", "value": "safe"},
                    {"path": "/git/identity", "operation": "clear"},
                ],
                "access_edits": [],
            }
            first = service.candidate(request)
            second = service.candidate(request)
            self.assertEqual(first["effective_declaration"]["mcp"]["permission_mode"], "safe")
            self.assertIsNone(first["effective_declaration"]["git"]["identity"])
            self.assertEqual(first["candidate_input_fingerprint"], second["candidate_input_fingerprint"])
            illegal = dict(request)
            illegal["field_edits"] = [{"path": "/tunnel/profile", "operation": "set", "value": "frontend-owned"}]
            with self.assertRaises(ManagerError):
                service.candidate(illegal)

    def test_access_edit_is_structured_and_manager_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InstanceRegistry(paths_for(root).registry_dir)
            target = root / "repo"
            target.mkdir()
            external = root / "External Models"
            external.mkdir()
            service = self._service(root, registry)
            payload = service.candidate(
                {
                    "candidate_request_version": 1,
                    "workspace_path": str(target),
                    "field_edits": [],
                    "access_edits": [{"operation": "add", "mode": "rw", "path": str(external)}],
                }
            )
            entry = payload["effective_declaration"]["access"]["read_write"][0]
            self.assertEqual(entry["alias"], "external-models")
            self.assertEqual(entry["path"], str(external))
            self.assertTrue(
                any(
                    item["path"].startswith("/access/read_write/0")
                    and item["source"] == "operator_override"
                    for item in payload["field_provenance"]
                )
            )

    def test_stale_copy_source_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InstanceRegistry(paths_for(root).registry_dir)
            target = root / "repo"
            target.mkdir()
            service = self._service(root, registry)
            with self.assertRaises(ManagerError) as raised:
                service.candidate(
                    {
                        "candidate_request_version": 1,
                        "workspace_path": str(target),
                        "copy_source_instance_id": "missing",
                        "field_edits": [],
                        "access_edits": [],
                    }
                )
            self.assertEqual(raised.exception.code, ErrorCode.INSTANCE_NOT_FOUND)

    def test_existing_workspace_preserves_custom_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InstanceRegistry(paths_for(root).registry_dir)
            workspace = root / "managed"
            workspace.mkdir()
            raw = desired_for("managed", workspace, mcp_port=7654, health_port=7070).to_dict()
            raw["tunnel"]["profile"] = "custom-existing-profile"
            registry.create(DesiredInstance.from_dict(raw))
            service = self._service(root, registry)
            payload = service.candidate(
                {
                    "candidate_request_version": 1,
                    "workspace_path": str(workspace),
                    "field_edits": [],
                    "access_edits": [],
                }
            )
            self.assertEqual(payload["existing_instance_id"], "managed")
            self.assertEqual(payload["effective_declaration"]["tunnel"]["profile"], "custom-existing-profile")


class AuthenticationTests(unittest.TestCase):
    def test_authentication_projection_never_returns_tunnel_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            env_file = root / "runtime.env"
            env_file.write_text("CONTROL_PLANE_API_KEY=VERY_SECRET_VALUE\n", encoding="utf-8")
            payload = authentication_status(
                paths,
                gh_binary=None,
                ssh_auth_sock=None,
                tunnel_env_file=str(env_file),
            )
            self.assertEqual(payload["authentication_status_version"], AUTHENTICATION_STATUS_VERSION)
            serialized = json.dumps(payload, sort_keys=True)
            self.assertNotIn("VERY_SECRET_VALUE", serialized)
            tunnel = next(item for item in payload["providers"] if item["provider"] == "tunnel-control-plane")
            self.assertEqual(tunnel["status"], "available")
            self.assertTrue(tunnel["capabilities"]["credential_name_present"])


if __name__ == "__main__":
    unittest.main()
