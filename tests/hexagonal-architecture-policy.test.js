"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");

const profilePath = join(
  __dirname,
  "..",
  "skills",
  "hexagonal-architecture-rust",
  "references",
  "validator.md",
);
const profile = readFileSync(profilePath, "utf8");
/** @type {string} */
let verificationBlock;

test.before(() => {
  const section = profile
    .split(/^## Retrieve and verify$/m)[1]
    ?.split(/^## /m)[0];
  assert.ok(section, "retrieve-and-verify section must exist");

  const blocks = [...section.matchAll(/^```console\n([\s\S]*?)^```$/gm)];
  assert.equal(blocks.length, 1, "verification must use one console block");
  verificationBlock = blocks[0]?.[1] ?? "";
  assert.ok(verificationBlock, "verification console block must not be empty");
});

test("binds the selected archive to the documented immutable digest", () => {
  assert.match(
    verificationBlock,
    /expected_sha256=de1d0d3c879defa1c7aa5616c2999800461532d67f5eff50d5512d88f6b82731/,
  );
  assert.match(
    verificationBlock,
    /expected_sha256=4af9f7ad02d7ee521c4226b252acb178c3ac1f09a5c400f3f94d4b3ee64e2f4b/,
  );
  assert.match(
    verificationBlock,
    /printf '%s {2}%s\\n' "\$expected_sha256" "\$artifact" \| shasum -a 256 -c -/,
  );
  assert.match(
    verificationBlock,
    /printf '%s {2}%s\\n' "\$expected_sha256" "\$artifact" \| sha256sum -c -/,
  );
  assert.doesNotMatch(verificationBlock, /SHA256SUMS/);
});

test("pins the attestation identity to the release workflow and source", () => {
  const normalized = verificationBlock.replace(/\\\n\s*/g, " ");
  const command = normalized
    .split("\n")
    .find((line) => line.startsWith('gh attestation verify "$artifact"'));
  assert.ok(command, "attestation verification command must exist");
  assert.match(
    command,
    /--repo nfma\/hexagonal-architecture-validator(?:\s|$)/,
  );
  assert.match(
    command,
    /--signer-workflow nfma\/hexagonal-architecture-validator\/\.github\/workflows\/release\.yml(?:\s|$)/,
  );
  assert.match(command, /--source-digest "\$source_digest"(?:\s|$)/);
  assert.match(command, /--source-ref "\$source_ref"(?:\s|$)/);
  assert.match(command, /--deny-self-hosted-runners(?:\s|$)/);
  assert.match(
    verificationBlock,
    /source_digest=7a625d7dc7491b63ac835719fee250759d4badae/,
  );
  assert.match(verificationBlock, /source_ref="refs\/tags\/\$version"/);
});

test("verifies integrity and provenance before extraction or execution", () => {
  const checksumIndex = verificationBlock.indexOf(
    'printf \'%s  %s\\n\' "$expected_sha256" "$artifact"',
  );
  const attestationIndex = verificationBlock.indexOf("gh attestation verify");
  const extractionIndex = verificationBlock.indexOf('tar -xzf "$artifact"');
  const executionIndex = verificationBlock.indexOf("./hav --version");

  assert.ok(checksumIndex >= 0, "checksum verification must exist");
  assert.ok(
    checksumIndex < attestationIndex,
    "checksum verification must precede provenance verification",
  );
  assert.ok(
    attestationIndex < extractionIndex,
    "provenance verification must precede extraction",
  );
  assert.ok(
    extractionIndex < executionIndex,
    "extraction must precede execution",
  );
});

test("stops before extraction when any verification command fails", () => {
  assert.equal(
    verificationBlock.split("\n").find((line) => line.trim()),
    "set -eu",
  );
  assert.doesNotMatch(verificationBlock, /\|\|\s*(?:true|:)/);
  assert.doesNotMatch(verificationBlock, /set\s+\+e/);
});

test("retains the verified binary after successful verification", () => {
  const executionIndex = verificationBlock.indexOf("./hav --version");
  const archiveRemovalIndex = verificationBlock.indexOf('rm -f "$artifact"');
  const retainedPathIndex = verificationBlock.indexOf(
    "printf 'Verified binary retained at %s/hav\\n' \"$work_dir\"",
  );
  const disableCleanupIndex = verificationBlock.indexOf("trap - EXIT");

  assert.ok(executionIndex >= 0, "verified binary execution must exist");
  assert.ok(
    executionIndex < archiveRemovalIndex,
    "archive removal must follow successful binary execution",
  );
  assert.ok(
    archiveRemovalIndex < retainedPathIndex,
    "retained binary path must be printed after archive removal",
  );
  assert.ok(
    retainedPathIndex < disableCleanupIndex,
    "successful verification must disable cleanup only after reporting the path",
  );
});
