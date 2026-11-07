# Python 3.10 standard-library-only acquisition validator.
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse

lock_path = pathlib.Path(sys.argv[1])
coding_lock = pathlib.Path(sys.argv[2])
if not lock_path.is_file() or lock_path.is_symlink():
    raise SystemExit('artifact lock is missing/not regular')

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result

try:
    data = json.loads(lock_path.read_text(encoding="utf-8"), object_pairs_hook=strict_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'invalid constant: {value}')))
except Exception as exc:
    raise SystemExit(f"invalid strict JSON: {exc}")

def exact_keys(value, expected, where):
    if not isinstance(value, dict):
        raise SystemExit(f"{where} must be an object")
    actual = set(value)
    expected = set(expected)
    if actual != expected:
        raise SystemExit(f"{where} fields mismatch: missing={sorted(expected-actual)} unknown={sorted(actual-expected)}")

exact_keys(data, {"schema_version", "platform", "architecture", "components"}, "root")
if type(data["schema_version"]) is not int or data["schema_version"] != 1:
    raise SystemExit("unsupported schema_version")
if data["platform"] != "linux" or data["architecture"] not in {"x86_64", "aarch64"}:
    raise SystemExit("unsupported lock platform/architecture")
if sys.argv[3] and data["architecture"] != sys.argv[3]:
    raise SystemExit("artifact lock architecture does not match the host")

components = data["components"]
exact_keys(components, {"python", "uv", "coding_tools_mcp", "tunnel_client"}, "components")
component_fields = {
    "version", "source_kind", "source_identity", "acquisition_location", "sha256",
    "platform", "architecture", "dependency_lock_identity", "dependencies",
    "validator_id", "expected_observable_identity",
}
dependency_fields = {"name", "version", "source_identity", "acquisition_location", "sha256"}
validators = {
    "python": "cpython-3.12",
    "uv": "uv-cli",
    "coding_tools_mcp": "coding-tools-mcp-0.5",
    "tunnel_client": "tunnel-client-0.0.15",
}
source_kinds = {"github_release_asset", "pypi_wheel"}
hex64 = re.compile(r"^[0-9a-f]{64}$")

for name, component in components.items():
    exact_keys(component, component_fields, f"components.{name}")
    for field in ("version", "source_kind", "source_identity", "acquisition_location", "sha256", "platform", "architecture", "validator_id", "expected_observable_identity"):
        if not isinstance(component[field], str) or not component[field]:
            raise SystemExit(f"components.{name}.{field} must be a non-empty string")
    if component["source_kind"] not in source_kinds:
        raise SystemExit(f"unsupported source_kind for {name}")
    if not hex64.fullmatch(component["sha256"]):
        raise SystemExit(f"invalid sha256 for {name}")
    if component["platform"] != data["platform"] or component["architecture"] != data["architecture"]:
        raise SystemExit(f"component platform/architecture mismatch for {name}")
    if component["validator_id"] != validators[name]:
        raise SystemExit(f"unsupported validator_id for {name}")
    parsed = urllib.parse.urlparse(component["acquisition_location"])
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit(f"acquisition_location must be HTTPS for {name}")
    dependency_lock = component["dependency_lock_identity"]
    if dependency_lock is not None and (not isinstance(dependency_lock, str) or not dependency_lock):
        raise SystemExit(f"invalid dependency_lock_identity for {name}")
    dependencies = component["dependencies"]
    if not isinstance(dependencies, list):
        raise SystemExit(f"dependencies must be an array for {name}")
    seen_dependencies = set()
    for index, dependency in enumerate(dependencies):
        exact_keys(dependency, dependency_fields, f"components.{name}.dependencies[{index}]")
        for field in dependency_fields:
            if not isinstance(dependency[field], str) or not dependency[field]:
                raise SystemExit(f"invalid dependency field {field} for {name}")
        if dependency["name"].lower() in seen_dependencies:
            raise SystemExit(f"duplicate dependency for {name}: {dependency['name']}")
        seen_dependencies.add(dependency["name"].lower())
        if not hex64.fullmatch(dependency["sha256"]):
            raise SystemExit(f"invalid dependency sha256 for {name}")
        parsed = urllib.parse.urlparse(dependency["acquisition_location"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise SystemExit(f"dependency acquisition_location must be HTTPS for {name}")

coding = components["coding_tools_mcp"]
if not coding_lock.is_file() or coding_lock.is_symlink():
    raise SystemExit("requirements-coding-tools.lock is missing/not regular")
digest = hashlib.sha256(coding_lock.read_bytes()).hexdigest()
expected_identity = f"requirements-coding-tools.lock@sha256:{digest}"
if coding["dependency_lock_identity"] != expected_identity:
    raise SystemExit("coding-tools dependency lock identity mismatch")
if len(coding["dependencies"]) != 1 or coding["dependencies"][0]["name"] != "PyJWT":
    raise SystemExit("coding-tools dependency closure must contain exactly pinned PyJWT")
coding_lock_text = coding_lock.read_text(encoding="utf-8")
if f"coding-tools-mcp=={coding['version']}" not in coding_lock_text or f"--hash=sha256:{coding['sha256']}" not in coding_lock_text:
    raise SystemExit("coding-tools component is inconsistent with its dependency lock")
dependency = coding["dependencies"][0]
if f"PyJWT=={dependency['version']}" not in coding_lock_text or f"--hash=sha256:{dependency['sha256']}" not in coding_lock_text:
    raise SystemExit("PyJWT artifact is inconsistent with coding-tools dependency lock")
