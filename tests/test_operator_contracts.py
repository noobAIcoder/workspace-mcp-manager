from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workspace_mcp_manager.domain import DesiredInstance
from workspace_mcp_manager.generation import ResourceGenerator
from workspace_mcp_manager.operator_contracts import (
    derive_operator_state,
    observed_plan_inputs,
    operator_template,
    plan_fingerprint,
    semantic_plan_operations,
)
from workspace_mcp_manager.operator_projection import OperatorProjectionService
from workspace_mcp_manager.paths import ManagerPaths
from workspace_mcp_manager.planning import (
    ObservationStatus,
    PlanItem,
    PlanOperation,
    ReconciliationPlan,
    ResourceObservation,
    UnitObservation,
)

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


class OperatorContractTests(unittest.TestCase):
    def test_plan_fingerprint_ignores_presentation_reason_but_changes_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = sample_v2_instance()
            raw["workspace_path"] = str(root / "workspace")
            desired = DesiredInstance.from_dict(raw)
            bundle = ResourceGenerator(paths_for(root)).generate(desired)
            first = ReconciliationPlan(
                instance_id="sample",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(PlanItem(PlanOperation.START, "service", "first wording", "mcp-unit"),),
                warnings=("human warning",),
            )
            reworded = ReconciliationPlan(
                instance_id="sample",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(PlanItem(PlanOperation.START, "service", "second wording", "mcp-unit"),),
                warnings=("different human warning",),
            )
            changed = ReconciliationPlan(
                instance_id="sample",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(PlanItem(PlanOperation.RESTART, "service", "same target", "mcp-unit"),),
                warnings=(),
            )
            self.assertEqual(plan_fingerprint(first, bundle), plan_fingerprint(reworded, bundle))
            self.assertNotEqual(plan_fingerprint(first, bundle), plan_fingerprint(changed, bundle))

    def test_plan_fingerprint_covers_observed_resource_identity(self) -> None:
        class Observer:
            def __init__(self, detail: str) -> None:
                self.detail = detail

            def observe_resource(self, resource):  # type: ignore[no-untyped-def]
                return ResourceObservation(ObservationStatus.DIFFERENT, self.detail)

            def observe_unit(self, unit_name: str) -> UnitObservation:
                return UnitObservation(True, True, True, self.detail)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = sample_v2_instance()
            raw["workspace_path"] = str(root / "workspace")
            desired = DesiredInstance.from_dict(raw)
            bundle = ResourceGenerator(paths_for(root)).generate(desired)
            plan = ReconciliationPlan(
                instance_id="sample",
                desired_fingerprint=desired.fingerprint(),
                valid=True,
                operations=(PlanItem(PlanOperation.UPDATE, "service", "same operation", "mcp-unit"),),
                warnings=(),
            )
            first_inputs = observed_plan_inputs(plan, bundle, Observer("current-a"))  # type: ignore[arg-type]
            second_inputs = observed_plan_inputs(plan, bundle, Observer("current-b"))  # type: ignore[arg-type]
            self.assertNotEqual(
                plan_fingerprint(plan, bundle, observed_inputs=first_inputs),
                plan_fingerprint(plan, bundle, observed_inputs=second_inputs),
            )

    def test_semantic_plan_operations_are_not_raw_json(self) -> None:
        plan = ReconciliationPlan(
            instance_id="sample",
            desired_fingerprint="a" * 64,
            valid=True,
            operations=(
                PlanItem(PlanOperation.CREATE, "/tmp/models", "x", "access-rw-models"),
                PlanItem(PlanOperation.RESTART, "coding-tools-mcp-sample.service", "x", "mcp-unit"),
            ),
            warnings=(),
        )
        items = semantic_plan_operations(plan)
        self.assertEqual(items[0]["operation"], "CREATE")
        self.assertIn("RW access", items[0]["description"])
        self.assertIn("Restart", items[1]["description"])

    def test_state_matrix_distinguishes_desired_stopped_from_failure(self) -> None:
        raw = sample_v2_instance()
        raw["lifecycle"] = {"deployment": "present", "runtime": "stopped"}
        desired = DesiredInstance.from_dict(raw)
        status = {
            "plan": {"valid": True, "operations": [{"operation": "NOOP"}]},
            "units": {
                "mcp-unit": {"query_exit_code": 0, "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead"},
                "tunnel-unit": {"query_exit_code": 0, "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead"},
            },
            "endpoints": {
                "mcp_discovery": {"http_status": None},
                "tunnel_ready": {"http_status": None},
            },
            "applied": {"desired_fingerprint": desired.fingerprint()},
            "recent_transaction_evidence": [],
        }
        state = derive_operator_state(desired, status)
        self.assertEqual(state["runtime"], "Stopped")
        self.assertEqual(state["health"], "Not applicable")
        self.assertEqual(state["tunnel"], "Not applicable")
        self.assertEqual(state["reconciliation"], "Applied")
        self.assertNotIn("unexpected runtime", " ".join(state["attention_reasons"]).lower())

    def test_unknown_probe_never_becomes_healthy_or_ready(self) -> None:
        desired = DesiredInstance.from_dict(sample_v2_instance())
        status = {
            "plan": {"valid": True, "operations": [{"operation": "NOOP"}]},
            "units": {
                "mcp-unit": {"query_exit_code": 0, "LoadState": "loaded", "ActiveState": "active", "SubState": "running"},
                "tunnel-unit": {"query_exit_code": 0, "LoadState": "loaded", "ActiveState": "active", "SubState": "running"},
            },
            "endpoints": {"mcp_discovery": {"http_status": None}, "tunnel_ready": {"http_status": None}},
            "applied": {},
            "recent_transaction_evidence": [],
        }
        state = derive_operator_state(desired, status)
        self.assertEqual(state["health"], "Unknown")
        self.assertEqual(state["tunnel"], "Unknown")

    def test_template_is_v2_non_secret_and_manager_path_derived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = operator_template(paths_for(Path(directory)))
        self.assertEqual(payload["config_version"], 2)
        self.assertEqual(payload["defaults"]["github"]["mode"], "disabled")
        self.assertIn("instance_id", payload["required_operator_fields"])
        serialized = str(payload).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("passphrase", serialized)

    def test_review_plan_exposes_manager_fingerprint_and_semantic_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            raw = sample_v2_instance()
            raw["workspace_path"] = str(root / "workspace")
            desired = DesiredInstance.from_dict(raw)
            from workspace_mcp_manager.registry import InstanceRegistry

            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            payload = OperatorProjectionService(paths, registry).review_plan("sample")
            self.assertEqual(len(payload["plan_fingerprint"]), 64)
            self.assertIsInstance(payload["semantic_operations"], list)
            self.assertEqual(payload["desired_fingerprint"], desired.fingerprint())

    def test_summary_uses_one_desired_snapshot_for_state_and_metadata(self) -> None:
        class FixedStatus:
            def __init__(self) -> None:
                self.seen: DesiredInstance | None = None

            def status(self, instance_id: str):  # type: ignore[no-untyped-def]
                raise AssertionError("summary must not re-read desired state through status()")

            def status_for(self, desired: DesiredInstance):  # type: ignore[no-untyped-def]
                self.seen = desired
                return {
                    "plan": {"valid": True, "operations": [{"operation": "NOOP"}]},
                    "plan_fingerprint": "p" * 64,
                    "units": {
                        "mcp-unit": {
                            "query_exit_code": 0,
                            "LoadState": "loaded",
                            "ActiveState": "inactive",
                            "SubState": "dead",
                        }
                    },
                    "endpoints": {"mcp_discovery": {}, "tunnel_ready": {}},
                    "applied": {"desired_fingerprint": desired.fingerprint()},
                    "recent_transaction_evidence": [],
                }

        from workspace_mcp_manager.registry import InstanceRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = paths_for(root)
            raw = sample_v2_instance()
            raw["workspace_path"] = str(root / "workspace")
            raw["lifecycle"] = {"deployment": "present", "runtime": "stopped"}
            desired = DesiredInstance.from_dict(raw)
            registry = InstanceRegistry(paths.registry_dir)
            registry.create(desired)
            status = FixedStatus()
            payload = OperatorProjectionService(paths, registry, status_service=status).summary("sample")  # type: ignore[arg-type]
            self.assertIsNotNone(status.seen)
            assert status.seen is not None
            self.assertEqual(status.seen.fingerprint(), desired.fingerprint())
            self.assertEqual(payload["workspace_path"], desired.workspace_path)
            self.assertEqual(payload["declaration_fingerprint"], desired.fingerprint())
            self.assertEqual(payload["desired_lifecycle"], "Present + Stopped")


if __name__ == "__main__":
    unittest.main()
