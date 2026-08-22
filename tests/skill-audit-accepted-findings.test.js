"use strict";

/* eslint-disable security/detect-non-literal-fs-filename -- Test files are confined to fresh temporary directories. */

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const { mkdtempSync, readFileSync, rmSync, writeFileSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");
const test = require("node:test");

/** @type {typeof import("../scripts/skill-audit-accepted-findings.mjs")} */
let acceptedFindings;
/** @typedef {"critical" | "high" | "medium" | "low" | "info"} Severity */
/** @typedef {{ skill: string, id: string, severity: Severity, file: string, line: number | null, evidence: string | null }} TestActualFinding */
/** @typedef {{ skill: string, id: string, severity: Severity, file: string, evidence: string | null, evidenceSha256: string | null, reason: string }} TestAcceptedFinding */
/** @typedef {{ skill: string, id: string, severity: Severity, file: string, line: number | null, evidence: string | null, reason: string }} VersionOneFinding */
/** @typedef {{ version: 1, selfAuditedSkills: unknown[], compatibilityOmissions: unknown[], acceptedFindings: VersionOneFinding[] }} VersionOneBaseline */
/** @type {string[]} */
const temporaryDirectories = [];

test.before(async () => {
  acceptedFindings =
    await import("../scripts/skill-audit-accepted-findings.mjs");
});

test.afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

/** @param {Partial<TestActualFinding>} overrides @returns {TestActualFinding} */
function actualFinding(overrides = {}) {
  return {
    skill: "example-skill",
    id: "TEST-001",
    severity: "high",
    file: "SKILL.md",
    line: 20,
    evidence: "dangerous example",
    ...overrides,
  };
}

/** @param {Partial<TestAcceptedFinding>} overrides @returns {TestAcceptedFinding} */
function acceptedFinding(overrides = {}) {
  const evidence = Object.hasOwn(overrides, "evidence")
    ? (overrides.evidence ?? null)
    : "dangerous example";
  /** @type {TestAcceptedFinding} */
  const finding = {
    skill: "example-skill",
    id: "TEST-001",
    severity: "high",
    file: "SKILL.md",
    evidence,
    evidenceSha256: acceptedFindings.hashFindingEvidence(evidence),
    reason: "Reviewed fixture.",
    ...overrides,
  };
  return finding;
}

/** @param {Partial<VersionOneBaseline>} overrides @returns {VersionOneBaseline} */
function versionOneBaseline(overrides = {}) {
  /** @type {VersionOneBaseline} */
  const baseline = {
    version: 1,
    selfAuditedSkills: [],
    compatibilityOmissions: [],
    acceptedFindings: [
      {
        skill: "example-skill",
        id: "TEST-001",
        severity: "high",
        file: "SKILL.md",
        line: 20,
        evidence: "dangerous example",
        reason: "Reviewed fixture.",
      },
    ],
    ...overrides,
  };
  return baseline;
}

test("accepts unchanged evidence after its source line moves", () => {
  const accepted = acceptedFinding();
  const result = acceptedFindings.evaluateAcceptedFindings(
    [accepted],
    [actualFinding({ line: 200 })],
  );

  assert.equal(result.acceptedMatches.length, 1);
  const [match] = result.acceptedMatches;
  assert.ok(match);
  assert.equal(match.actualFinding.line, 200);
  assert.deepEqual(result.staleFindings, []);
  assert.deepEqual(result.ambiguousMatches, []);
});

test("rejects changed evidence instead of inheriting acceptance", () => {
  const result = acceptedFindings.evaluateAcceptedFindings(
    [acceptedFinding()],
    [actualFinding({ evidence: "changed evidence" })],
  );

  assert.deepEqual(result.acceptedMatches, []);
  assert.equal(result.staleFindings.length, 1);
  assert.deepEqual(result.ambiguousMatches, []);
});

test("fails closed when identical evidence occurs more than once", () => {
  const result = acceptedFindings.evaluateAcceptedFindings(
    [acceptedFinding()],
    [actualFinding({ line: 20 }), actualFinding({ line: 40 })],
  );

  assert.deepEqual(result.acceptedMatches, []);
  assert.deepEqual(result.staleFindings, []);
  assert.equal(result.ambiguousMatches.length, 1);
  const [ambiguousMatch] = result.ambiguousMatches;
  assert.ok(ambiguousMatch);
  assert.deepEqual(
    ambiguousMatch.actualFindings.map(({ line }) => line),
    [20, 40],
  );
});

test("scopes identical evidence to its skill, rule, severity, and file", () => {
  const accepted = acceptedFinding();
  const result = acceptedFindings.evaluateAcceptedFindings(
    [accepted],
    [
      actualFinding(),
      actualFinding({ file: "references/example.md", line: 20 }),
      actualFinding({ id: "TEST-002", line: 20 }),
    ],
  );

  assert.equal(result.acceptedMatches.length, 1);
  const [match] = result.acceptedMatches;
  assert.ok(match);
  assert.equal(match.actualFinding.file, "SKILL.md");
});

test("migrates line-addressed entries to validated content fingerprints", () => {
  const migrated =
    acceptedFindings.migrateAcceptedFindingBaseline(versionOneBaseline());

  assert.equal(migrated.version, 2);
  const [migratedFinding] = migrated.acceptedFindings;
  assert.ok(migratedFinding);
  assert.equal(Object.hasOwn(migratedFinding, "line"), false);
  assert.equal(
    migratedFinding.evidenceSha256,
    acceptedFindings.hashFindingEvidence("dangerous example"),
  );
  assert.doesNotThrow(() =>
    acceptedFindings.validateAcceptedFindingBaseline(migrated),
  );
});

test("refuses a migration that collapses two line entries to one identity", () => {
  const [finding] = versionOneBaseline().acceptedFindings;
  assert.ok(finding);
  const baseline = versionOneBaseline({
    acceptedFindings: [finding, { ...finding, line: 40 }],
  });

  assert.throws(
    () => acceptedFindings.migrateAcceptedFindingBaseline(baseline),
    /Duplicate accepted finding/,
  );
});

test("rejects a changed reviewed evidence digest", () => {
  const baseline = {
    version: 2,
    acceptedFindings: [acceptedFinding({ evidenceSha256: "0".repeat(64) })],
  };

  assert.throws(
    () => acceptedFindings.validateAcceptedFindingBaseline(baseline),
    /does not match its reviewed evidence/,
  );
});

test("requires explicit migration before version 1 can be audited", () => {
  assert.throws(
    () =>
      acceptedFindings.validateAcceptedFindingBaseline(versionOneBaseline()),
    /npm run migrate:skill-audit-baseline/,
  );
});

test("migrates a baseline file through the public command", () => {
  const directory = mkdtempSync(join(tmpdir(), "skill-audit-migration-"));
  temporaryDirectories.push(directory);
  const baselinePath = join(directory, "baseline.json");
  writeFileSync(baselinePath, JSON.stringify(versionOneBaseline()));

  const result = spawnSync(
    process.execPath,
    [
      resolve("scripts/migrate-skill-audit-baseline.mjs"),
      "--baseline",
      baselinePath,
    ],
    { encoding: "utf8" },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Migrated 1 accepted findings/);
  const migrated = JSON.parse(readFileSync(baselinePath, "utf8"));
  assert.equal(migrated.version, 2);
  assert.equal(Object.hasOwn(migrated.acceptedFindings[0], "line"), false);
});
