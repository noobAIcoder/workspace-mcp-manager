# PM3.1.2 — Repository-Aware Development Toolchain Setup

## Status

Normative implementation specification.

PM3.1.2 extends PM3.1 context-aware instance setup with repository-aware
development-toolchain discovery and readiness. It does not weaken explicit
`mcp.exec_path` / `mcp.external_roots` authority and does not source shell
startup files.

## Objective

For supported repositories, setup MUST reconcile:

```text
repository runtime requirements
    +
installed host toolchains
    +
declared MCP execution environment
    ↓
development-toolchain readiness
```

Initial supported repository contract:

```text
package.json
  engines.node
  packageManager = pnpm@<exact-semver>
```

Initial host-toolchain contract:

```text
~/.nvm/versions/node/<version>/
```

## Public projection

Add:

```text
development_toolchain_projection_version = 1
```

Workspace discovery MUST expose a `development_toolchain` projection containing
only non-secret repository requirements, installed-version metadata, selection,
declaration compatibility, and deterministic recommendations.

Because workspace discovery gains a new public field:

```text
discovery_version = 2
```

`candidate_request_version` remains `1`; the request schema is unchanged.
`candidate_version` remains `1`; the candidate response shape remains the same
and carries the versioned discovery projection it already contains.

## Repository inspection

Discovery MAY read only bounded, non-secret manifest metadata required for the
toolchain projection. For the initial contract:

```text
<workspace>/package.json
```

The file MUST be a non-symlink regular file and MUST be bounded to 256 KiB.
Malformed, oversized, or unsupported requirements MUST be reported as
unresolved/unsupported readiness rather than guessed.

No repository script may execute during discovery.

## Node range contract

The initial deterministic range evaluator supports whitespace-conjoined
comparators equivalent to:

```text
>=24.18.0 <25
>24.0.0 <=24.99.99
=24.18.0
24.18.0
24.x
24.*
```

`||`, hyphen ranges, prerelease policy, caret, tilde, and other npm semver
syntax remain unsupported until explicitly qualified. Unsupported syntax MUST
produce `NODE_RANGE_UNSUPPORTED`; it MUST NOT silently select a Node version.

## NVM observation

The manager MUST inspect bounded immediate children of:

```text
~/.nvm/versions/node/
```

For each non-symlink directory it MAY execute bounded version probes for:

```text
node --version
npm --version
corepack --version
pnpm --version
```

using:

```text
HOME=<real account home>
PATH=<node-root>/bin:/usr/local/bin:/usr/bin:/bin
COREPACK_ENABLE_NETWORK=0
COREPACK_DEFAULT_TO_LATEST=0
```

No shell startup file or `nvm.sh` may be sourced. Toolchain discovery MUST NOT
implicitly download or install Node or a package manager; an unavailable
Corepack-managed pnpm release remains unavailable/setup-required.

## Selection

Selection MUST be deterministic.

1. Filter installed roots by the supported Node requirement.
2. If the repository specifies `pnpm@<exact-semver>`, require that exact
   observed pnpm version for `ready` classification.
3. Select the highest compatible Node semantic version among fully compatible
   roots.
4. If no fully compatible root exists but a Node-compatible root exists,
   report package-manager setup required; do not classify ready.
5. If no Node-compatible root exists, report Node setup required.

Initial reason codes:

```text
NO_NODE_REQUIREMENT
PACKAGE_JSON_INVALID
NODE_RANGE_UNSUPPORTED
NODE_VERSION_UNAVAILABLE
PACKAGE_MANAGER_UNAVAILABLE
PACKAGE_MANAGER_VERSION_MISMATCH
DECLARATION_TOOLCHAIN_MISMATCH
READY
```

## Declaration recommendation

When a fully compatible NVM root exists, the deterministic recommendation is:

```text
mcp.exec_path
  -> selected <node-root>/bin first

mcp.external_roots
  -> include complete selected <node-root>
```

Any previous NVM version-bin/root entries are replaced by the selected version;
unrelated explicit path entries and external roots are preserved.

The complete Node version root, not only `bin`, is required because npm,
corepack, pnpm, and installed package implementations may resolve files below
`lib/node_modules`.

## Candidate behavior

For a **new unmatched workspace** with a fully compatible selected toolchain,
the manager candidate builder SHOULD apply the recommendation automatically as
workspace-derived/manager-recommended configuration before frontend edits.

Explicit `/mcp/exec_path` and `/mcp/external_roots` edits remain higher
precedence and may override the recommendation.

For an **existing matched instance**, discovery MUST report any declaration
mismatch but MUST NOT silently rewrite the authoritative declaration merely by
opening the workspace. Remediation uses the ordinary candidate -> preview ->
reviewed update flow.

If no fully compatible toolchain exists, declaration creation remains possible
under the existing PM3.1 model, but the UI MUST report development setup as not
ready and MUST NOT label the environment ready.

## Textual UX

New-instance Runtime MUST show:

```text
Development toolchain
Node requirement
package-manager requirement
installed compatible selection or Setup required
```

Existing-instance Overview MUST show the same authoritative workspace
projection and whether the current declaration includes the selected toolchain.

Credential/token material is unrelated and MUST NOT enter this projection.

## ElectroCAD acceptance

ElectroCAD currently declares:

```text
engines.node = >=24.18.0 <25
packageManager = pnpm@11.17.0
```

Qualification MUST prove that Node `24.15.0` is rejected as incompatible.
After a compatible host toolchain exists, the manager MUST recommend/adopt the
matching NVM root without manual systemd editing.

Final MCP execution qualification MUST prove:

```text
node --version satisfies >=24.18.0 <25
pnpm --version == 11.17.0
corepack executable/version observable when present
pnpm check succeeds
pnpm build succeeds
```
