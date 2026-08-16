"use strict";

/* eslint-disable security/detect-non-literal-fs-filename -- Tests operate only inside fixed repository paths and fresh temporary directories. */

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
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

/** @param {string} root */
function initializeLegacyCheckout(root) {
  runGit(root, ["init", "--quiet"]);
  runGit(root, ["config", "user.email", "test@example.com"]);
  runGit(root, ["config", "user.name", "Release Test"]);
  writeFileSync(join(root, "tracked.txt"), "before\n");
  runGit(root, ["add", "tracked.txt"]);
  runGit(root, ["commit", "--quiet", "-m", "Initial fixture"]);
  return runGit(root, ["rev-parse", "HEAD"]).trim();
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
      /pin digest\/size mismatch: expected .* got/,
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

test("rejects corrupted embedded rules even when executable identity matches", () => {
  const { descriptor } = verifier.loadPinnedDescriptor();
  const root = temporaryDirectory();
  try {
    const source = readFileSync(executablePath, "utf8");
    const markerIndex = source.indexOf(
      `"${descriptor.executable.embeddedRulesSha256}"`,
    );
    assert.notEqual(markerIndex, -1);
    const encodedMatch = source
      .slice(markerIndex)
      // The fixture starts from the size- and digest-pinned release executable.
      // eslint-disable-next-line security/detect-unsafe-regex
      .match(/,[$A-Za-z_][$\w]*=(\[(?:"[A-Za-z0-9+/=]*",?)+\])\.join\(""\)/);
    assert.ok(encodedMatch);
    const matchIndex = encodedMatch.index;
    assert.ok(matchIndex !== undefined);
    const encodedChunks = encodedMatch[1];
    assert.ok(encodedChunks);
    const encoded = /** @type {string[]} */ (JSON.parse(encodedChunks)).join(
      "",
    );
    const rules = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
    rules.categories.behavioral.description += " corrupted";
    const corruptedEncoded = Buffer.from(JSON.stringify(rules)).toString(
      "base64",
    );
    const payloadOffset =
      markerIndex + matchIndex + encodedMatch[0].indexOf(encodedChunks);
    const corruptedSource =
      source.slice(0, payloadOffset) +
      JSON.stringify([corruptedEncoded]) +
      source.slice(payloadOffset + encodedChunks.length);
    const corruptedBytes = Buffer.from(corruptedSource);
    const corruptedExecutable = join(root, "skill-audit.mjs");
    writeFileSync(corruptedExecutable, corruptedBytes);
    const corruptedDescriptor = {
      ...descriptor,
      executable: {
        ...descriptor.executable,
        sha256: verifier.sha256(corruptedBytes),
        sizeBytes: corruptedBytes.length,
      },
    };

    assert.throws(
      () => verifier.verifyExecutable(corruptedDescriptor, corruptedExecutable),
      /embedded rules digest mismatch: expected [0-9a-f]{64}, got [0-9a-f]{64}/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects an executable that starts a child process during import", () => {
  const { descriptor } = verifier.loadPinnedDescriptor();
  const root = temporaryDirectory();
  try {
    const sideEffect = String.raw`
if (!process.argv.includes("--version")) {
  const { execFileSync } = await import("node:child_process");
  execFileSync(process.execPath, ["--version"]);
}
`;
    const sideEffectBytes = Buffer.concat([
      readFileSync(executablePath),
      Buffer.from(sideEffect),
    ]);
    const sideEffectExecutable = join(root, "skill-audit.mjs");
    writeFileSync(sideEffectExecutable, sideEffectBytes);
    const sideEffectDescriptor = {
      ...descriptor,
      executable: {
        ...descriptor.executable,
        sha256: verifier.sha256(sideEffectBytes),
        sizeBytes: sideEffectBytes.length,
      },
    };

    assert.throws(
      () =>
        verifier.verifyExecutable(sideEffectDescriptor, sideEffectExecutable),
      /Import attempted prohibited side effect: execFileSync/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("re-downloads and verifies a replacement for a corrupt installation", async () => {
  const { descriptor } = verifier.loadPinnedDescriptor();
  const root = realpathSync(temporaryDirectory());
  const installedExecutable = join(root, "dist", "skill-audit.mjs");
  mkdirSync(dirname(installedExecutable), { recursive: true });
  writeFileSync(installedExecutable, "corrupted");
  const releaseBytes = readFileSync(executablePath);
  /** @type {string[]} */
  const events = [];
  const originalWarn = console.warn;
  console.warn = (message) => events.push(`warn:${message}`);
  try {
    await verifier.installExecutable(descriptor, {
      executable: installedExecutable,
      corpusRoot: fixtureRoot,
      fetchImpl: async (url) => {
        events.push(`fetch:${url}`);
        return new Response(releaseBytes, {
          headers: { "content-length": String(releaseBytes.length) },
        });
      },
    });

    assert.match(events[0] ?? "", /failed verification: .*identity mismatch/);
    assert.match(events[1] ?? "", /^fetch:https:\/\/github\.com\//);
    assert.equal(
      verifier.sha256(readFileSync(installedExecutable)),
      descriptor.executable.sha256,
    );
  } finally {
    console.warn = originalWarn;
    rmSync(root, { recursive: true, force: true });
  }
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

test("legacy cleanup rejects the wrong commit", () => {
  const root = temporaryDirectory();
  try {
    const head = initializeLegacyCheckout(root);
    const expectedCommit = "0".repeat(40);
    assert.notEqual(head, expectedCommit);
    assert.throws(
      () => verifier.cleanupLegacyVendor(root, { expectedCommit }),
      (error) =>
        error instanceof Error &&
        error.message.includes(`expected commit ${expectedCommit}`) &&
        error.message.includes(`got ${head}`) &&
        error.message.includes("manually remove the retained checkout"),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("legacy cleanup rejects an unstaged patch", () => {
  const root = temporaryDirectory();
  try {
    const head = initializeLegacyCheckout(root);
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
    const patchSha256 = verifier.sha256(patch.stdout);
    assert.throws(
      () => verifier.cleanupLegacyVendor(root, { expectedCommit: head }),
      (error) =>
        error instanceof Error &&
        error.message.includes("expected no unstaged patch") &&
        error.message.includes(`got SHA-256 ${patchSha256}`) &&
        error.message.includes("manually remove the retained checkout"),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("legacy cleanup rejects staged changes", () => {
  const root = temporaryDirectory();
  try {
    const head = initializeLegacyCheckout(root);
    writeFileSync(join(root, "tracked.txt"), "after\n");
    runGit(root, ["add", "tracked.txt"]);
    assert.throws(
      () => verifier.cleanupLegacyVendor(root, { expectedCommit: head }),
      (error) =>
        error instanceof Error &&
        error.message.includes("expected no staged paths") &&
        error.message.includes('got ["tracked.txt"]') &&
        error.message.includes("manually remove the retained checkout"),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("legacy cleanup rejects untracked files", () => {
  const root = temporaryDirectory();
  try {
    const head = initializeLegacyCheckout(root);
    writeFileSync(join(root, "untracked.txt"), "untracked\n");
    assert.throws(
      () => verifier.cleanupLegacyVendor(root, { expectedCommit: head }),
      (error) =>
        error instanceof Error &&
        error.message.includes("expected no untracked files") &&
        error.message.includes('["untracked.txt"]') &&
        error.message.includes("manually remove the retained checkout"),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("legacy cleanup removes the exact clean checkout", () => {
  const root = temporaryDirectory();
  const head = initializeLegacyCheckout(root);
  assert.doesNotThrow(() =>
    verifier.cleanupLegacyVendor(root, { expectedCommit: head }),
  );
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
