# Pinned hav validator profile

Status: verified release available. This profile supplies Rust structural
evidence for the language-neutral `hexagonal-architecture` skill. It never
replaces that skill's semantic or behavioral checks and must not be used for
another language.

Use it only when the Cargo workspace declares its intended roles and rules.

## Pinned release

- Repository:
  https://github.com/nfma/hexagonal-architecture-validator
- Release: `v0.1.1`
- Release commit: `7a625d7dc7491b63ac835719fee250759d4badae`
- Configuration schema: `1`
- JSON report schema: `1`
- Verified: 2026-08-16 by Nuno (`nfma`) and the skill integration flow

- macOS Apple Silicon:
  `hexagonal-architecture-validator-v0.1.1-aarch64-apple-darwin.tar.gz`
  with SHA-256
  `de1d0d3c879defa1c7aa5616c2999800461532d67f5eff50d5512d88f6b82731`.
- Linux x86-64:
  `hexagonal-architecture-validator-v0.1.1-x86_64-unknown-linux-gnu.tar.gz`
  with SHA-256
  `4af9f7ad02d7ee521c4226b252acb178c3ac1f09a5c400f3f94d4b3ee64e2f4b`.

The release uses a signed annotated tag. GitHub artifact attestations bind both
archives to the release workflow at the pinned commit and `refs/tags/v0.1.1`.

## Retrieve and verify

Obtain approval before downloading or installing a binary. Select the archive
for the current platform, download it from the pinned release, then bind it to
the documented digest, release workflow, source commit, tag, and hosted runner
before extraction:

```console
set -eu
version=v0.1.1
source_digest=7a625d7dc7491b63ac835719fee250759d4badae
source_ref="refs/tags/$version"
platform="$(uname -s)-$(uname -m)"
case "$platform" in
  Darwin-arm64)
    artifact=hexagonal-architecture-validator-v0.1.1-aarch64-apple-darwin.tar.gz
    expected_sha256=de1d0d3c879defa1c7aa5616c2999800461532d67f5eff50d5512d88f6b82731
    ;;
  Linux-x86_64)
    artifact=hexagonal-architecture-validator-v0.1.1-x86_64-unknown-linux-gnu.tar.gz
    expected_sha256=4af9f7ad02d7ee521c4226b252acb178c3ac1f09a5c400f3f94d4b3ee64e2f4b
    ;;
  *)
    printf 'Unsupported platform: %s\n' "$platform" >&2
    exit 64
    ;;
esac
base=https://github.com/nfma/hexagonal-architecture-validator
base="$base/releases/download/$version"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
cd "$work_dir"
curl --proto '=https' --tlsv1.2 -fLO "$base/$artifact"
case "$platform" in
  Darwin-arm64)
    printf '%s  %s\n' "$expected_sha256" "$artifact" | shasum -a 256 -c -
    ;;
  Linux-x86_64)
    printf '%s  %s\n' "$expected_sha256" "$artifact" | sha256sum -c -
    ;;
esac
gh attestation verify "$artifact" \
  --repo nfma/hexagonal-architecture-validator \
  --signer-workflow nfma/hexagonal-architecture-validator/.github/workflows/release.yml \
  --source-digest "$source_digest" \
  --source-ref "$source_ref" \
  --deny-self-hosted-runners
tar -xzf "$artifact"
./hav --version
```

The command rejects unsupported platforms and uses a temporary directory. Move
the verified binary into a project-owned tool directory only when the task
requires repeated use; do not install it globally by default.

## Configure and run

Review `hav.toml` before execution. Its roles and rules must express the actual
workspace rather than a desired folder template.

The built-in preset has one generic adapter role and does not enforce the
driven-adapter-to-application direction. Declare driving and driven adapter
roles separately and forbid driven adapters from depending on application use
cases while allowing driven-port and domain-data contracts.

A preset-only result, or a blind spot that could hide that edge, is an evidence
gap even on exit `0`.

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

Treat exit `2` and every reported analysis unknown as missing structural
evidence. Reports are deterministic and include stable module, dependency,
finding, exemption, analysis-error, limitation, and summary data.

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

Combine the result with semantic and behavioral evidence. Recheck this profile
when release assets, checksums, schemas, exit behavior, analysis scope, or
ownership changes. Owner: Nuno (`nfma`). Recheck by 2026-11-22.
