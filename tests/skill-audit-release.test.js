"use strict";

/* eslint-disable security/detect-non-literal-fs-filename -- Tests operate only inside fixed repository paths and fresh temporary directories. */

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const {
  cpSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} = require("node:fs");
const { tmpdir } = require("node:os");
const { dirname, join, resolve } = require("node:path");
const { pathToFileURL } = require("node:url");
const test = require("node:test");

const repositoryRoot = resolve(__dirname, "..");
const pinPath = join(repositoryRoot, ".skill-audit-release.json");
const documentationRoot = join(repositoryRoot, "skills/skill-audit");
const executablePath = join(
  repositoryRoot,
  "vendor/skill-audit/dist/skill-audit.mjs",
);
const fixtureRoot = join(repositoryRoot, "tests/fixtures/skill-audit-release");
/** @type {typeof import("../scripts/verify-skill-audit-release.mjs")} */
let verifier;

test.before(async () => {
  verifier = await import(
    pathToFileURL(
      join(repositoryRoot, "scripts/verify-skill-audit-release.mjs"),
    ).href
  );
});

function temporaryDirectory() {
  return mkdtempSync(join(tmpdir(), "skill-audit-release-test-"));
}

/** @param {string} root @param {string[]} args */
function runGit(root, args) {
  const result = spawnSync("/usr/bin/git", args, {
    cwd: root,
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `git ${args.join(" ")} failed\n${result.stdout}${result.stderr}`,
    );
  }
  return result.stdout;
}

test("pins the byte-exact v0.10.2 release descriptor", () => {
  const { bytes, descriptor } = verifier.loadPinnedDescriptor();
  assert.equal(verifier.sha256(bytes), verifier.PIN_SHA256);
  assert.equal(descriptor.version, "0.10.2");

  const root = temporaryDirectory();
  try {
    const released = join(root, "skill-audit-v0.10.2-release.json");
    writeFileSync(released, bytes);
    verifier.verifyReleaseDescriptorBytes(released);

    writeFileSync(released, Buffer.concat([bytes, Buffer.from("\n")]));
    assert.throws(
      () => verifier.verifyReleaseDescriptorBytes(released),
      /differs from the release descriptor asset/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verifies every consumer documentation file and aggregate", () => {
  const { descriptor } = verifier.loadPinnedDescriptor();
  assert.equal(
    verifier.verifyDocumentation(descriptor),
    descriptor.documentation.upstreamDocsSha256,
  );

  const root = temporaryDirectory();
  try {
    cpSync(documentationRoot, root, { recursive: true });
    const firstReference = descriptor.documentation.files[1]?.path;
    assert.ok(firstReference);
    const target = join(root, ...firstReference.split("/"));
    writeFileSync(target, `${readFileSync(target, "utf8")}changed\n`);
    assert.throws(
      () => verifier.verifyDocumentation(descriptor, root),
      (error) =>
        error instanceof Error &&
        error.message.includes(`digest mismatch for ${firstReference}`),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verifies the installed executable and fixed legacy and portable corpus", () => {
  const { descriptor } = verifier.loadPinnedDescriptor();
  assert.doesNotThrow(() =>
    verifier.verifyExecutable(descriptor, executablePath),
  );
  assert.doesNotThrow(() =>
    verifier.verifyFixedCorpus(executablePath, fixtureRoot),
  );
});

test("released dual-read preserves CTX-002 through CTX-006", async () => {
  const { descriptor } = verifier.loadPinnedDescriptor();
  verifier.verifyExecutable(descriptor, executablePath);
  const loaded = await import(pathToFileURL(executablePath).href);
  /** @type {Array<[string, string[], string]>} */
  const cases = [
    [
      "missing reads",
      [
        "skill-audit-context-requires: explicit_user_intent",
        "skill-audit-context-writes: audit_summary",
        "skill-audit-confirmation: on-risk",
      ],
      "CTX-002",
    ],
    [
      "missing requires",
      [
        "skill-audit-context-reads: user_goal",
        "skill-audit-context-writes: audit_summary",
        "skill-audit-confirmation: on-risk",
      ],
      "CTX-003",
    ],
    [
      "missing writes",
      [
        "skill-audit-context-reads: user_goal",
        "skill-audit-context-requires: explicit_user_intent",
        "skill-audit-confirmation: on-risk",
      ],
      "CTX-004",
    ],
    [
      "missing confirmation",
      [
        "skill-audit-context-reads: user_goal",
        "skill-audit-context-requires: explicit_user_intent",
        "skill-audit-context-writes: audit_summary",
      ],
      "CTX-005",
    ],
    [
      "overbroad reads",
      [
        "skill-audit-context-reads: user_goal, all_context",
        "skill-audit-context-requires: explicit_user_intent",
        "skill-audit-context-writes: audit_summary",
        "skill-audit-confirmation: on-risk",
      ],
      "CTX-006",
    ],
  ];

  const root = temporaryDirectory();
  try {
    for (const [name, metadata, expectedFinding] of cases) {
      const skillRoot = join(root, name.replaceAll(" ", "-"));
      cpSync(join(fixtureRoot, "portable"), skillRoot, { recursive: true });
      writeFileSync(
        join(skillRoot, "SKILL.md"),
        [
          "---",
          "name: fixture",
          "description: A fixture used to validate released skill-audit behavior.",
          "metadata:",
          ...metadata.map((line) => `  ${line}`),
          "---",
          "",
          "# Fixture",
          "",
          "Use the fixture safely.",
          "",
        ].join("\n"),
      );
      const spec = loaded.validateSkillSpec(skillRoot, "fixture");
      const security = loaded.auditSecurity(
        {
          name: "fixture",
          path: skillRoot,
          scope: "project",
          agents: ["shared"],
        },
        spec.manifest,
      );
      const contextFindings = /** @type {Array<{ id: string }>} */ (
        security.findings
      );
      assert.deepEqual(
        contextFindings
          .filter((finding) => finding.id.startsWith("CTX-"))
          .map((finding) => finding.id),
        [expectedFinding],
        name,
      );
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("legacy cleanup accepts only the named dirty patch", () => {
  const root = temporaryDirectory();
  try {
    runGit(root, ["init", "--quiet"]);
    runGit(root, ["config", "user.email", "test@example.com"]);
    runGit(root, ["config", "user.name", "Release Test"]);
    writeFileSync(join(root, "tracked.txt"), "before\n");
    runGit(root, ["add", "tracked.txt"]);
    runGit(root, ["commit", "--quiet", "-m", "Initial fixture"]);
    const head = runGit(root, ["rev-parse", "HEAD"]).trim();
    writeFileSync(join(root, "tracked.txt"), "after\n");
    const patch = spawnSync(
      "/usr/bin/git",
      ["diff", "--no-ext-diff", "--binary"],
      {
        cwd: root,
        encoding: null,
      },
    );
    assert.equal(patch.status, 0);
    const acceptedPatchSha256 = verifier.sha256(patch.stdout);
    assert.notEqual(acceptedPatchSha256, verifier.LEGACY_PATCH_SHA256);

    assert.throws(
      () =>
        verifier.cleanupLegacyVendor(root, {
          expectedCommit: head,
          acceptedPatchSha256: "0".repeat(64),
        }),
      /unapproved patch/,
    );
    runGit(root, ["add", "tracked.txt"]);
    assert.throws(
      () =>
        verifier.cleanupLegacyVendor(root, {
          expectedCommit: head,
          acceptedPatchSha256,
        }),
      /staged changes/,
    );
    runGit(root, ["reset", "--quiet"]);
    assert.doesNotThrow(() =>
      verifier.cleanupLegacyVendor(root, {
        expectedCommit: head,
        acceptedPatchSha256,
      }),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("release downloads are bounded by the pinned executable size", async () => {
  const exact = await verifier.readBoundedResponse(
    new Response(Buffer.from("exact"), {
      headers: { "content-length": "5" },
    }),
    5,
  );
  assert.equal(exact.toString("utf8"), "exact");

  await assert.rejects(
    verifier.readBoundedResponse(new Response(Buffer.from("too long")), 3),
    /exceeded the pinned size/,
  );
  await assert.rejects(
    verifier.readBoundedResponse(new Response(Buffer.from("short")), 6),
    /size mismatch/,
  );
  await assert.rejects(
    verifier.readBoundedResponse(
      new Response(Buffer.from("exact"), {
        headers: { "content-length": "999" },
      }),
      5,
    ),
    /size mismatch/,
  );
});

test("the tracked pin remains the configured source of truth", () => {
  assert.equal(verifier.sha256(readFileSync(pinPath)), verifier.PIN_SHA256);
  assert.equal(
    dirname(executablePath),
    join(repositoryRoot, "vendor/skill-audit/dist"),
  );
});
