# Rust static validator profile

Status: verified release available.

This is one language-specific implementation of the
[static validation contract](static-validation.md). Use it only for Rust
repositories. Never apply it to JavaScript, TypeScript, Java, or another
language; use that ecosystem's validator instead. For example, TypeScript and
JavaScript repositories can use dependency-cruiser.

Use this profile only when the Rust repository declares its intended
architectural roles and rules. The validator adds deterministic static
dependency evidence; it does not decide whether the declared boundaries are
semantically correct.

## Pinned release

- Repository:
  https://github.com/nfma/hexagonal-architecture-validator
- Release: `v0.1.1`
- Release commit: `7a625d7dc7491b63ac835719fee250759d4badae`
- Configuration schema: `1`
- JSON report schema: `1`
- Verified: 2026-08-16 by Nuno (`nfma`) and the skill integration flow

| Target | Archive | SHA-256 |
| --- | --- | --- |
| macOS Apple Silicon | `hexagonal-architecture-validator-v0.1.1-aarch64-apple-darwin.tar.gz` | `de1d0d3c879defa1c7aa5616c2999800461532d67f5eff50d5512d88f6b82731` |
| Linux x86-64 | `hexagonal-architecture-validator-v0.1.1-x86_64-unknown-linux-gnu.tar.gz` | `4af9f7ad02d7ee521c4226b252acb178c3ac1f09a5c400f3f94d4b3ee64e2f4b` |

The release uses a signed annotated tag. GitHub artifact attestations bind both
archives to the release workflow at the pinned commit and `refs/tags/v0.1.1`.

## Retrieve and verify

Obtain approval before downloading or installing a binary. Select the archive
for the current platform, download it and `SHA256SUMS` from the pinned release,
then verify both the checksum and provenance before extraction:

```console
version=v0.1.1
artifact=hexagonal-architecture-validator-v0.1.1-aarch64-apple-darwin.tar.gz
base=https://github.com/nfma/hexagonal-architecture-validator
base="$base/releases/download/$version"
curl --proto '=https' --tlsv1.2 -fLO "$base/$artifact"
curl --proto '=https' --tlsv1.2 -fLO "$base/SHA256SUMS"
grep "  $artifact\$" SHA256SUMS | shasum -a 256 -c -
gh attestation verify "$artifact" \
  --repo nfma/hexagonal-architecture-validator
tar -xzf "$artifact"
./hav --version
```

On Linux, select the `x86_64-unknown-linux-gnu` archive and replace `shasum -a
256` with `sha256sum`. Prefer a temporary or project-owned tool directory over
a global installation.

## Run

Review `hav.toml` before execution. It must express intended module roles and
forbidden dependencies; never infer a passing configuration from the current
graph and present it as compliance.

The built-in hexagonal preset has one generic adapter role and forbids no
adapter dependency on application modules, so a preset-only run does not check
the driven-adapter invariant in `static-validation.md`. To check it, declare
driving and driven adapter roles separately and add an explicit rule forbidding
driven-adapter dependencies on application use-case and orchestration modules,
while still allowing the driven-port and domain-data roles.

A preset-only result, or any configuration with a single generic adapter role
and no such rule, is incomplete evidence for that invariant: report the gap even
on exit `0`. Do the same when a blind spot below — macros, dynamic dispatch,
external crates, or an unanalyzed block-local module — could hide a
driven-to-application edge on the paths under review.

The preset also requires roles named `core`, `application`, `port`, `adapter`,
and `composition-root`, and every declared role must match at least one
discovered module. A repository without those distinctions should declare its
own roles and rules without the preset rather than force the structure.

```console
./hav check --root . --config hav.toml --format json
```

Exit states are stable:

- `0`: analysis completed with no violations;
- `1`: analysis completed and found violations; and
- `2`: configuration, discovery, parsing, resolution, or reporting failed.

Treat exit `2`, analysis diagnostics, unsupported source relationships, and
missing role matches as unknown evidence, never as a pass. Reports are ordered
deterministically and include stable module, dependency, finding, exemption,
analysis-error, limitation, and summary data.

## Verified integration gate

The published macOS archive was re-downloaded and verified on 2026-08-16.
Checksums and GitHub attestations passed. The release binary reported `hav
0.1.1`; compliant, violating, malformed-config, and unresolved-analysis
fixtures returned `0`, `1`, `2`, and `2` respectively. Repeated text and JSON
violation reports were byte-identical.

## Known blind spots

- `cfg` predicates and Cargo feature selection are not evaluated.
- Macros, derives, and attribute macros are not expanded. `include!` is fatal;
  strict mode rejects other unsupported item-position macros.
- Public re-export visibility is incomplete and fails closed when a route could
  cross a forbidden boundary.
- Block-local module bodies are not analyzed. A `use` through one fails closed.
- External crates, build-script output, method calls, dynamic dispatch, and
  runtime service lookup do not create analyzed edges.

Combine the result with the semantic and behavioral criteria in
`architecture-criteria.md`. Recheck this profile when the release assets,
checksums, schema versions, exit behavior, analysis scope, or repository owner
changes.
