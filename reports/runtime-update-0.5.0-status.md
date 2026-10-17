# Bundled runtime update qualification — 2026-10-17

Repository update only; publishing, release promotion, installation, instance
migration, service restarts, and deployment were not performed.

## Selected artifacts

- coding-tools-mcp 0.5.0 wheel: SHA-256
  `2d728ba41e49cad71a20228e228bb36492617d0caea5b6ae73d8e7c5186a1edc`.
  Downloaded bytes match the [PyPI release metadata](https://pypi.org/pypi/coding-tools-mcp/0.5.0/json).
- Full tunnel-client 0.0.15 Linux amd64 ZIP: SHA-256
  `8c836dc5d68d68b663d9a5c5b28ff9fa780d9f7a3fffb1c306880b8f32fab5f1`.
  Downloaded bytes match both the release asset digest and
  [published SHA256SUMS.txt](https://github.com/openai/tunnel-client/releases/download/v0.0.15/SHA256SUMS.txt).
  The annotated release tag resolves to commit
  `a390c168ff1b2d14e73a95991c186c6aba3ff5a0`.
- PyJWT remains 2.10.1. The [upstream dependency declaration](https://github.com/xyTom/coding-tools-mcp/blob/v0.5.0/pyproject.toml)
  requires PyJWT >=2.8. Hash-checked offline installation of the downloaded
  wheels into a temporary Python 3.12 environment succeeded; imports report
  coding-tools-mcp 0.5.0 and PyJWT 2.10.1.

The downloaded tunnel ZIP contains exactly LICENSE, NOTICE, cloudflared,
cloudflared-manifest.json, tunnel-client,
tunnel-client-v0.0.15-linux-amd64-licenses.txt, and
tunnel-client-v0.0.15-linux-amd64.spdx.json. The installer requires precisely
this layout. It continues to install the full client artifact.

Python, uv, Textual, manager version, declaration/public command schemas,
permission modes, and runtime execution policy are unchanged. No experimental
coding-tools flags were enabled. Historical qualification reports are unchanged.

## Repository validation

Python 3.12.13 was used after an initial Python 3.10 run failed the repository's
minimum Python requirement. The applicable suite ran 342 tests: **314 passed,
28 skipped**, with 25 environment-dependent test methods excluded as detailed
below. The focused compatibility/archive/distribution-runtime run also passed
(32 tests, before adding the independent repository-lock consistency test).

Passing coverage includes:

- The real repository lock's strict installer validation, dependency-lock digest,
  version pins, and dependency hashes.
- Seven-file tunnel archive acceptance and missing, unexpected, duplicate, and
  unsafe path rejection using the actual provisioning function in isolation.
- Managed GitHub verification against sessionless 0.5.0 fixtures, including
  explicit timeout/output bounds; legacy 0.2.2 and 0.3.0 regressions remain.
- 0.5.0 cross-request command handles, output reads, and termination through
  discovered tool schemas and structured results; legacy continuity remains.
- Distribution runtime path selection, including release-specific paths.

Python compilation of src/tests, Bash syntax checks for bootstrap and all
scripts, and `git diff --check` passed.

## Environment limits and remaining gate

The full unfiltered Python 3.12 attempt ran 365 tests before the final lock test
and additional GitHub test were discoverable: 28 failures, two errors, and 28
skips. These are not claimed as a passing full-suite result.

- 23 distribution installer test methods were excluded from the final applicable
  run: this host is Ubuntu 22.04 ARM64; the installer requires Ubuntu 24.04
  x86_64. The unfiltered installer failures hit the Ubuntu host check, including
  declaration-preserving upgrades and interruption/recovery tests. Those
  fixtures now use the new versions but their end-to-end qualification remains
  pending on a supported host.
- Two tests were excluded because sandbox Unix socket binding raises
  `PermissionError`: external-agent socket preconditions and the ElectroCad
  SSH rewrite dry run.
- 28 Textual Pilot tests skip because the isolated frontend runtime is absent.
- The downloaded amd64 tunnel binary cannot execute on this ARM64 host
  (`Exec format error`). Its expected observable identity follows the release
  tag and upstream version implementation; execution is still a live gate.

Run the full suite on the supported host, the isolated frontend tests, and
`scripts/qualify_distribution_wsl.sh` before release promotion. Live WSL
qualification must verify actual artifact identities, declaration-preserving
upgrades, release-specific paths, recovery, and runtime behavior. This report
does not replace that gate or claim live deployment compatibility.
