import { createHash } from "node:crypto";

export const ACCEPTED_FINDING_BASELINE_VERSION = 2;

/**
 * @typedef {"critical" | "high" | "medium" | "low" | "info"} Severity
 * @typedef {{ skill: string, id: string, severity: Severity, file: string, evidence: string | null, evidenceSha256: string | null, reason: string }} AcceptedFinding
 * @typedef {{ skill: string, id: string, severity: Severity, file: string, line: number | null, evidence: string | null }} ActualFinding
 * @typedef {{ fingerprint: string, acceptedFinding: AcceptedFinding, actualFinding: ActualFinding }} AcceptedMatch
 * @typedef {{ fingerprint: string, acceptedFinding: AcceptedFinding, actualFindings: ActualFinding[] }} AmbiguousMatch
 */

const severities = new Set(["critical", "high", "medium", "low", "info"]);

/** @param {string | null} evidence */
export function hashFindingEvidence(evidence) {
  if (evidence === null) return null;
  return createHash("sha256").update(evidence).digest("hex");
}

/** @param {AcceptedFinding | ActualFinding} finding */
export function acceptedFindingFingerprint(finding) {
  const evidenceSha256 =
    "evidenceSha256" in finding
      ? finding.evidenceSha256
      : hashFindingEvidence(finding.evidence);
  return JSON.stringify([
    finding.skill,
    finding.id,
    finding.severity,
    finding.file,
    evidenceSha256,
  ]);
}

/** @param {unknown} value @param {string} label */
function requireObject(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return /** @type {Record<string, unknown>} */ (value);
}

/** @param {Record<string, unknown>} value @param {string} key @param {string} label */
function requireNonEmptyString(value, key, label) {
  const field = value[key];
  if (typeof field !== "string" || field.trim() === "") {
    throw new TypeError(`${label}.${key} must be a non-empty string`);
  }
  return field;
}

/** @param {Record<string, unknown>} value @param {string} label */
function parseEvidence(value, label) {
  const evidence = value.evidence;
  if (evidence !== null && typeof evidence !== "string") {
    throw new TypeError(`${label}.evidence must be a string or null`);
  }
  return evidence;
}

/** @param {unknown} value @param {string} label */
function parseAcceptedFinding(value, label) {
  const finding = requireObject(value, label);
  const severity = requireNonEmptyString(finding, "severity", label);
  if (!severities.has(severity)) {
    throw new TypeError(`${label}.severity is unsupported: ${severity}`);
  }
  if (Object.hasOwn(finding, "line")) {
    throw new Error(`${label}.line is not allowed in baseline version 2`);
  }
  const evidence = parseEvidence(finding, label);
  const evidenceSha256 = finding.evidenceSha256;
  if (
    evidenceSha256 !== null &&
    (typeof evidenceSha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(evidenceSha256))
  ) {
    throw new TypeError(
      `${label}.evidenceSha256 must be a lowercase SHA-256 digest or null`,
    );
  }
  const expectedDigest = hashFindingEvidence(evidence);
  if (evidenceSha256 !== expectedDigest) {
    throw new Error(
      `${label}.evidenceSha256 does not match its reviewed evidence: expected ${String(expectedDigest)}, got ${String(evidenceSha256)}`,
    );
  }

  return /** @type {AcceptedFinding} */ ({
    skill: requireNonEmptyString(finding, "skill", label),
    id: requireNonEmptyString(finding, "id", label),
    severity,
    file: requireNonEmptyString(finding, "file", label),
    evidence,
    evidenceSha256,
    reason: requireNonEmptyString(finding, "reason", label),
  });
}

/**
 * Validate the version 2 accepted-finding section and return normalized entries.
 *
 * @param {unknown} baseline
 * @returns {AcceptedFinding[]}
 */
export function validateAcceptedFindingBaseline(baseline) {
  const parsedBaseline = requireObject(baseline, "skill-audit baseline");
  if (parsedBaseline.version !== ACCEPTED_FINDING_BASELINE_VERSION) {
    if (parsedBaseline.version === 1) {
      throw new Error(
        "Skill-audit baseline version 1 must be migrated with `npm run migrate:skill-audit-baseline`",
      );
    }
    throw new Error(
      `Unsupported skill-audit baseline version: ${String(parsedBaseline.version)}`,
    );
  }
  if (!Array.isArray(parsedBaseline.acceptedFindings)) {
    throw new TypeError(
      "skill-audit baseline acceptedFindings must be an array",
    );
  }

  const acceptedFindings = parsedBaseline.acceptedFindings.map(
    (finding, index) =>
      parseAcceptedFinding(finding, `acceptedFindings[${index}]`),
  );
  const fingerprints = new Set();
  for (const finding of acceptedFindings) {
    const fingerprint = acceptedFindingFingerprint(finding);
    if (fingerprints.has(fingerprint)) {
      throw new Error(`Duplicate accepted finding: ${fingerprint}`);
    }
    fingerprints.add(fingerprint);
  }
  return acceptedFindings;
}

/**
 * Convert a line-addressed version 1 baseline to content-addressed version 2.
 *
 * @param {unknown} baseline
 */
export function migrateAcceptedFindingBaseline(baseline) {
  const parsedBaseline = requireObject(baseline, "skill-audit baseline");
  if (parsedBaseline.version !== 1) {
    throw new Error(
      `Only skill-audit baseline version 1 can be migrated, got ${String(parsedBaseline.version)}`,
    );
  }
  if (!Array.isArray(parsedBaseline.acceptedFindings)) {
    throw new TypeError(
      "skill-audit baseline acceptedFindings must be an array",
    );
  }

  const acceptedFindings = parsedBaseline.acceptedFindings.map(
    (rawFinding, index) => {
      const label = `acceptedFindings[${index}]`;
      const finding = requireObject(rawFinding, label);
      const evidence = parseEvidence(finding, label);
      const line = finding.line;
      if (
        line !== null &&
        (typeof line !== "number" || !Number.isInteger(line) || line < 1)
      ) {
        throw new TypeError(`${label}.line must be a positive integer or null`);
      }
      return {
        skill: requireNonEmptyString(finding, "skill", label),
        id: requireNonEmptyString(finding, "id", label),
        severity: requireNonEmptyString(finding, "severity", label),
        file: requireNonEmptyString(finding, "file", label),
        evidence,
        evidenceSha256: hashFindingEvidence(evidence),
        reason: requireNonEmptyString(finding, "reason", label),
      };
    },
  );
  const migrated = {
    ...parsedBaseline,
    version: ACCEPTED_FINDING_BASELINE_VERSION,
    acceptedFindings,
  };
  validateAcceptedFindingBaseline(migrated);
  return migrated;
}

/**
 * Resolve accepted findings against the current audit without using line as identity.
 *
 * @param {AcceptedFinding[]} acceptedFindings
 * @param {ActualFinding[]} actualFindings
 */
export function evaluateAcceptedFindings(acceptedFindings, actualFindings) {
  /** @type {Map<string, AcceptedFinding>} */
  const acceptedByFingerprint = new Map();
  for (const finding of acceptedFindings) {
    const fingerprint = acceptedFindingFingerprint(finding);
    if (acceptedByFingerprint.has(fingerprint)) {
      throw new Error(`Duplicate accepted finding: ${fingerprint}`);
    }
    acceptedByFingerprint.set(fingerprint, finding);
  }

  /** @type {Map<string, ActualFinding[]>} */
  const actualByFingerprint = new Map();
  for (const finding of actualFindings) {
    const fingerprint = acceptedFindingFingerprint(finding);
    const matches = actualByFingerprint.get(fingerprint) ?? [];
    matches.push(finding);
    actualByFingerprint.set(fingerprint, matches);
  }

  /** @type {AcceptedMatch[]} */
  const acceptedMatches = [];
  /** @type {AcceptedFinding[]} */
  const staleFindings = [];
  /** @type {AmbiguousMatch[]} */
  const ambiguousMatches = [];
  for (const [fingerprint, acceptedFinding] of acceptedByFingerprint) {
    const actualFindingsForFingerprint =
      actualByFingerprint.get(fingerprint) ?? [];
    if (actualFindingsForFingerprint.length === 0) {
      staleFindings.push(acceptedFinding);
    } else if (actualFindingsForFingerprint.length === 1) {
      const actualFinding = actualFindingsForFingerprint[0];
      if (!actualFinding) {
        throw new Error(`Missing actual finding for ${fingerprint}`);
      }
      acceptedMatches.push({
        fingerprint,
        acceptedFinding,
        actualFinding,
      });
    } else {
      ambiguousMatches.push({
        fingerprint,
        acceptedFinding,
        actualFindings: actualFindingsForFingerprint,
      });
    }
  }

  return { acceptedMatches, staleFindings, ambiguousMatches };
}
