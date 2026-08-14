# PM3.1 — Context-aware instance setup

Status: **NORMATIVE IMPLEMENTATION SPECIFICATION**

PM3.1 refines the qualified PM3 Textual workflow so new-instance setup is
manager-discovered, manager-composed, provenance-aware, and low-input. PM3.1
does not change declaration `config_version = 2`, lifecycle authority,
registry persistence semantics, Git/Access ownership semantics, authentication
ownership, systemd authority, or PM3 concurrency/review protections.

```text
launch context
-> manager discovery
-> manager candidate construction
-> operator edits intent only
-> manager preview
-> review
-> create
-> optional reviewed apply
```

## Contract versions

```text
template_version              = 2
discovery_version             = 1
candidate_request_version     = 1
candidate_version             = 1
port_projection_version       = 1
authentication_status_version = 1
```

Existing PM3 projection and plan-fingerprint versions remain unchanged unless
their public schemas/fingerprint inputs change.

## Authority boundary

The manager MUST own workspace canonicalization, instance matching/suggestion,
candidate precedence/copy/adoption, provenance, endpoint interpretation,
recommendation/collision logic, tunnel-profile derivation, Git interpretation,
Access desired-state construction, authentication-status projection, and
preview/reconciliation.

The TUI MUST retain only selected workspace/copy context and structured
operator edit intent. It MUST NOT synthesize an effective declaration,
interpret Git/host state, merge Access arrays, derive tunnel profiles, or
implement collision rules. The TUI MAY enumerate directories only to let the
operator select a path.

Secret values MUST NOT cross public manager contracts or enter TUI state,
fingerprints, logs, persistence, diagnostics, or qualification evidence.
Authentication remains external; PM3.1 exposes only bounded non-secret status
and provider-owned setup actions.

## Workspace discovery

`instance discover <path>` MUST be read-only and bounded.

For an existing selected directory the manager MUST:

1. resolve the input to an absolute physical path;
2. detect a local Git worktree root without remote network access;
3. use that root as the workspace when present, otherwise the resolved input;
4. use the physical normalized workspace path as canonical identity;
5. compare registered declarations using the same identity rule;
6. return `none`, `single`, or `conflict` for workspace matches.

Nested repository paths MUST resolve to the worktree root. Lexically different
symlink paths resolving to the same physical workspace MUST compare equal.
Discovery MUST NOT scan arbitrary sibling/ancestor repositories and MUST NOT
rewrite declarations merely because their lexical workspace path differs.

Discovery response fields MUST include:

```text
discovery_version
input_path
resolved_path
workspace_path
workspace_identity
repository_detected
repository_root
repository_basename
instance_match
instance_id_suggestion
local_git
authentication_status
warnings
discovery_fingerprint
```

The discovery fingerprint MUST cover non-secret evidence relevant to candidate
construction.

## Instance-ID suggestion

Use repository basename, otherwise workspace basename. Normalize by Unicode
NFKD, ASCII-only decomposition output, lowercase, maximal non-`[a-z0-9]` runs
to `-`, trim `-`, fallback `workspace`, then enforce the existing 63-character
limit. Registry collisions use the first available suffix `-2`, `-3`, ... with
base truncation as required. An existing-workspace match takes precedence over
new-ID suffixing.

## Candidate request and edit grammar

`instance candidate` consumes canonical JSON on stdin:

```text
candidate_request_version = 1
workspace_path
copy_source_instance_id?
field_edits[]
access_edits[]
```

`field_edits` use RFC 6901 declaration paths and `set | clear`. `set` requires
`value`; `clear` is legal only for declaration fields whose semantics permit
absence/null. Absent edit means no operator override. Unknown/non-editable
paths, malformed pointers, invalid values, illegal clears, and direct
`/tunnel/profile` edits for new candidates MUST be rejected.

`access_edits` use structured `add | remove | update` operations. The manager
MAY derive a normalized alias from a selected folder basename when `add` omits
one, but final alias/path uniqueness and overlap validation remains manager
authority.

Candidate responses MUST include:

```text
candidate_version
candidate_input_fingerprint
effective_declaration
candidate_fingerprint
field_provenance[]
discovery_fingerprint
copy_source
copy_source_fingerprint
port_projection
warnings
unresolved_required_operator_fields
```

Candidate input fingerprints MUST cover canonical workspace identity,
copy-source identity/fingerprint, normalized edits, and relevant contract
versions; they MUST exclude secret material.

## Candidate precedence and copy policy

Effective precedence is:

```text
operator override
-> adoptable target-workspace evidence
-> reusable copy-source configuration
-> manager-derived/recommended value
-> template default
```

Observed-but-not-adoptable evidence remains visible and MUST NOT become desired
configuration.

New-instance identity-sensitive fields MUST NOT be copied:

```text
instance_id
workspace_path
lifecycle
mcp.port
tunnel.id
tunnel.profile
tunnel.health_port
github
git
agent
```

Reusable copy allowlist:

```text
mcp.binary
mcp.host
mcp.permission_mode
mcp.shell_env_inherit
mcp.exec_path
mcp.external_roots
tunnel.binary
tunnel.health_host
tunnel.env_file
recovery.*
access.read_only
access.read_write
```

Copied Access remains desired configuration and MUST pass target-workspace
validation.

## Provenance

Provenance uses RFC 6901 paths. Each meaningful effective leaf/item SHOULD
report `path`, `source`, `source_ref`, `status`, and `reason`.

Sources:

```text
template_default
copy_source
workspace_discovery
manager_derived
manager_recommendation
operator_override
```

Statuses:

```text
effective
overridden
unresolved
invalidated
provisional
observed_only
```

Access provenance MUST be per effective item/field rather than container-only.

## Endpoint projection and collision model

`host ports` MUST return declared endpoints, host-observed listeners, merged
projection, and explicit listener-observation state `available | partial |
unavailable`. Observation execution/parse failure MUST NOT become an
authoritative empty listener set.

All collision-sensitive paths (recommendation, candidate, preview, planner,
create/apply) MUST use the same manager endpoint-overlap function.

Two endpoints conflict only when TCP ports match and bind scopes overlap.
Minimum rules:

```text
same normalized address -> conflict
0.0.0.0 -> conflicts with every IPv4 bind on that port
:: -> conflicts with every IPv6 bind on that port
observed :: with unknown IPV6_V6ONLY -> conservatively conflicts with IPv4
IPv4-mapped IPv6 -> normalize to IPv4
127.0.0.1 vs ::1 -> distinct absent wildcard/dual-stack overlap
non-IP names -> exact normalized match conflicts; otherwise overlap is unknown
```

No DNS/network resolution is permitted merely to claim an endpoint clear.
Cross-purpose collisions (MCP versus tunnel health) are collisions.

Preferred recommendation ranges are MCP `7654..7999` and tunnel health
`7070..7499`, ascending. Recommendations exclude declaration conflicts,
affirmatively observed conflicts, and candidate-internal conflicts. They are
advisory, do not reserve ports, and are recomputed/revalidated.

When listener observation is not authoritative, recommendations MAY be
returned but MUST be marked provisional and MUST NOT be presented as `free`.
Collision state is `clear | conflict | unknown`. New/changed endpoint mutation
MUST fail closed on `unknown`; an unchanged endpoint already attributable to
the same managed instance MAY retain existing PM3 ownership behavior.

## Tunnel profile

For new candidates the manager derives:

```text
workspace-mcp-<effective-instance-id>
```

`tunnel.profile` is not a required operator field and is not editable in the
normal new-instance workflow. Changing instance ID reconstructs the candidate
and therefore the derived profile. Persisted custom profiles for existing
declarations remain unchanged.

## Local Git discovery and adoption

Normal discovery MUST be local and independent of remote reachability probes.
It MAY inspect Git executable/repository/root, bounded working-tree metadata,
repository-local and effective identity with scope/origin, ownership markers,
remote names, deterministic selected remote (`origin`, then lexical first),
sanitized remote identity/transport, GitHub CLI presence, SSH-agent capability,
and non-secret authentication status.

Normal discovery MUST NOT run `git ls-remote`.

Raw remote URLs that may contain userinfo/secrets MUST NOT be returned. Public
projection MAY contain only sanitized remote name, transport, host, and safe
repository identity/path.

Repository-local complete identity MAY be adopted only when existing reversible
Git ownership semantics classify it as safely adoptable. Inherited/global
identity is observation-only. A supported existing GitHub remote MAY seed
desired name/protocol only when reversible adoption is possible. Unsupported or
foreign state remains visible but is not rewritten.

`github`/`agent` desired state MAY be derived only for an adoptable target
remote when compatible external capability is affirmatively available.
Authentication absence never makes workspace discovery fail; instead desired
Git management remains unresolved where required.

## Access desired-state provenance

Automatic candidate Access may originate only from a matching authoritative
declaration, explicit copy-source declaration, or operator Access edits.
Applied/recovery residue, ownership markers without declaration intent,
neighbor/subdirectories, arbitrary symlinks, or unowned access-like paths MUST
NOT be silently adopted.

## Authentication status

Authentication projection version is `1`. Provider entries expose only:

```text
provider
status = available | unavailable | unknown | not_applicable
verification_scope
non-secret capability metadata
setup_action?
reason
```

Setup actions MUST be manager-projected provider-owned/documented HTTPS
destinations, MUST NOT mutate manager state, and MUST NOT embed credentials.

## Textual workflow

New-instance information architecture is:

```text
Workspace -> Runtime -> Tunnel -> Git & Access -> Review
```

Workspace uses launch CWD only as input to manager discovery. Copy source is a
manager-populated dropdown. Workspace selection has a directory picker whose
only authority is filesystem navigation. An existing workspace match exposes
`Open existing instance` prominently.

Runtime places Permission mode before MCP port and shows manager-projected known
ports below it. Tunnel accepts Tunnel ID, shows manager-recommended health port
and known ports, and shows external authentication status; it has no editable
tunnel-profile field. Git & Access shows manager observation/adoption and
desired Access state. Review shows manager effective declaration, provenance,
candidate input/candidate fingerprints, validation, collision state, warnings,
semantic plan, and reviewed plan fingerprint.

The frontend stores operator edit intent only. Discovery/candidate asynchronous
results MUST pass both PM3 generation suppression and input-identity checks.
Workspace-context changes invalidate target-bound edits but preserve reusable
runtime/recovery edits, all revalidated by a reconstructed candidate.

## Verification and completion

Pure verification MUST cover discovery/identity, deterministic IDs, candidate
grammar/precedence/copy/provenance/fingerprints, Git adoption/secrecy, Access
provenance, endpoint projection/overlap/recommendations/unknown safety,
tunnel-profile derivation/custom preservation, authentication status, and
dirty/stale semantics. Textual Pilot MUST cover the PM3.1 information
architecture and manager-authoritative reconstruction paths.

Live WSL qualification uses a disposable unregistered Git workspace plus the
existing disposable `manager-qual` baseline, performs no external
authentication mutation, restores baseline, and runs PM3/PM2 regressions.

Completion culminates in:

```text
PM3_1_CONTEXT_AWARE_UX_QUALIFICATION=PASS
```
