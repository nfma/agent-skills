# Grading-key and raw-evidence custody

This evaluation uses two repositories and an explicit human recovery boundary.
The public `agent-skills` repository contains only answer-free digests and the
proof. The private `nfma/agent-skills-evidence` repository contains the
encrypted key and custody metadata; its private immutable releases hold raw
response, trace, and stderr archives.

The GPG recovery identity and an independent plaintext-key backup belong in
separate Nuno-controlled 1Password items. Do not commit vault names, item URIs,
passphrases, secret keys, or plaintext criteria. Retrieving either item is a
human-authorized action, not an agent search step.

## Required private layout

```text
sync-traycer-notion-trigger/v1/
  README.md
  recipient-public.asc
  key.json.gpg
  custody-manifest.json
```

The custody manifest records the public recipient fingerprint, canonical
`key_sha256`, `ciphertext_sha256`, ciphertext size, `bundle_commit`, later
`freeze_commit`, public key-manifest digest, author and reviewer identities,
timestamps, recovery result, and raw-release coordinates. It contains no check
text or secret locator.

Before sealing, enable immutable releases in the private repository and add a
tag ruleset that prevents update and deletion of the evidence-tag pattern. The
archive SHA-256 is the integrity authority even when a provider setting or API
signal is unavailable.

## Key sealing and recovery

Create a dedicated OpenPGP encryption identity with expiry `0`. Encrypt only to
its exact fingerprint, using batch mode and an explicit trust model:

```sh
gpg --batch --yes --trust-model always \
  --recipient "$RECIPIENT_FINGERPRINT" \
  --output key.json.gpg \
  --encrypt key.json
```

The answer-free public manifest and encrypted object must be pushed before the
first paid session. The key author must not subsequently edit a bound skill,
suite, runner, threshold, schema, or binding test.

Nuno performs the recovery drill outside agent context. Supply temporary paths
to the exported recovery identity and passphrase file, then run:

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/verify_private_evidence.py recover-key \
  --ciphertext /private/path/key.json.gpg \
  --key-manifest /public/path/key-manifest.json \
  --secret-key /private/path/recovery-secret.asc \
  --passphrase-file /private/path/passphrase
```

The verifier creates a temporary GPG home, recomputes the canonical plaintext
digest, kills its GPG daemons with `gpgconf --homedir ... --kill all`, and then
removes the temporary plaintext and home. It never prints the key.

## Raw archive recovery

The archive contains `run-manifest.json`, `key-manifest.json`, the precomputed
schedule, and all relative `responses/` and `traces/` paths. It contains neither
the grading key nor decrypted criteria. Upload and digest-verify the archive as
a draft private release, publish it, download it into a clean directory, and
then make the original run root inaccessible before grading.

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/verify_private_evidence.py archive \
  --archive /downloaded/raw-evidence.zip \
  --raw-evidence-sha256 "$RAW_EVIDENCE_SHA256" \
  --raw-evidence-size "$RAW_EVIDENCE_SIZE" \
  --key /temporary/recovered/key.json \
  --output /temporary/proof-report.json \
  --private-release-tag "$RELEASE_TAG" \
  --private-asset-name raw-evidence.zip
```

GitHub is the only raw-evidence backup. Repository deletion, account loss, or a
provider immutability failure can make re-grading impossible and require a new
paid run. Record that accepted risk with every private release.
