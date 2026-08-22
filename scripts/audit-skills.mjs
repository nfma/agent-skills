#!/usr/bin/env node

import {
  existsSync,
  readFileSync,
  readdirSync,
  realpathSync,
  statSync,
} from "node:fs";
import { basename, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import {
  acceptedFindingFingerprint,
  evaluateAcceptedFindings,
  validateAcceptedFindingBaseline,
} from "./skill-audit-accepted-findings.mjs";
import { evaluateCompatibilityPolicy } from "./skill-compatibility-policy.mjs";

/**
 * @typedef {{
 *   id: string,
 *   category: string,
 *   asi: string,
 *   severity: "critical" | "high" | "medium" | "low" | "info",
 *   file: string,
 *   line?: number,
 *   message: string,
 *   evidence?: string,
 *   recommendation?: string
 * }} Finding
 */
/** @typedef {{ name: string, path: string, scope: "project", agents: string[] }} SkillInfo */
/** @typedef {{ name: string, description: string, compatibility?: string, content: string, files: string[] }} SkillManifest */
/**
 * @typedef {{
 *   skill: SkillInfo,
 *   manifest?: SkillManifest,
 *   specFindings: Finding[],
 *   securityFindings: Finding[],
 *   piiFindings: Finding[],
 *   complianceFindings: Finding[],
 *   intelFindings: Finding[],
 *   riskScore: number,
 *   riskLevel: "safe" | "risky" | "dangerous" | "malicious"
 * }} GroupedAuditResult
 */
/** @typedef {{ skill: string, id: string, severity: Finding["severity"], file: string, line: number | null, evidence: string | null }} NormalizedFinding */
/** @typedef {{ skill: string, id: string, severity: Finding["severity"], file: string, evidence: string | null, evidenceSha256: string | null, reason: string }} AcceptedFinding */
/** @typedef {{ skill: string, reason: string }} SelfAuditedSkill */
/** @typedef {{ skill: string, reason: string }} CompatibilityOmission */
/** @typedef {{ version: 2, acceptedFindings: AcceptedFinding[], selfAuditedSkills: SelfAuditedSkill[], compatibilityOmissions: CompatibilityOmission[] }} AuditBaseline */
/** @typedef {{ scanDependencies(skillPath: string): Finding[] }} DependencyModule */
/** @typedef {{ reportGroupedResults(results: GroupedAuditResult[], options: { json: boolean, verbose: boolean, threshold: number, mode: string, block: boolean }): boolean }} ReporterModule */
/** @typedef {{ groupSecurityFindings(findings: Finding[]): { securityFindings: Finding[], piiFindings: Finding[], complianceFindings: Finding[] }, createGroupedAuditResult(skill: SkillInfo, manifest: SkillManifest | undefined, specFindings: Finding[], securityFindings: Finding[], piiFindings: Finding[], complianceFindings: Finding[], intelFindings: Finding[]): GroupedAuditResult }} ScoringModule */
/** @typedef {{ auditSecurity(skill: SkillInfo, manifest?: SkillManifest): { findings: Finding[] } }} SecurityModule */
/** @typedef {{ validateSkillSpec(skillPath: string, dirName: string): { manifest?: SkillManifest, findings: Finding[] } }} SpecModule */

const repositoryRoot = fileURLToPath(new URL("..", import.meta.url));
const skillsRoot = resolve(repositoryRoot, "skills");
const baselinePath = resolve(repositoryRoot, ".skill-audit-baseline.json");
const ignoredDirectories = new Set([".git", "node_modules"]);
const skillAudit = await import(
  new URL("../vendor/skill-audit/dist/skill-audit.mjs", import.meta.url).href
);
const { scanDependencies } = /** @type {DependencyModule} */ (skillAudit);
const { reportGroupedResults } = /** @type {ReporterModule} */ (skillAudit);
const { createGroupedAuditResult, groupSecurityFindings } =
  /** @type {ScoringModule} */ (skillAudit);
const { auditSecurity } = /** @type {SecurityModule} */ (skillAudit);
const { validateSkillSpec } = /** @type {SpecModule} */ (skillAudit);

/** @returns {AuditBaseline} */
function loadBaseline() {
  if (!existsSync(baselinePath)) {
    return {
      version: 2,
      acceptedFindings: [],
      selfAuditedSkills: [],
      compatibilityOmissions: [],
    };
  }

  const baseline = /** @type {AuditBaseline} */ (
    JSON.parse(readFileSync(baselinePath, "utf8"))
  );
  baseline.acceptedFindings = validateAcceptedFindingBaseline(baseline);

  return baseline;
}

/** @param {string} root @param {string} candidate */
function isWithinRoot(root, candidate) {
  const relativePath = relative(root, candidate);
  return (
    relativePath === "" ||
    (relativePath !== ".." &&
      !relativePath.startsWith(`..${sep}`) &&
      !isAbsolute(relativePath))
  );
}

/** @param {SkillInfo} skill @param {Finding} finding @returns {NormalizedFinding} */
function normalizeFinding(skill, finding) {
  const skillPrefix = `${skill.path}/`;

  return {
    skill: skill.name,
    id: finding.id,
    severity: finding.severity,
    file: finding.file.startsWith(skillPrefix)
      ? finding.file.slice(skillPrefix.length)
      : finding.file,
    line: finding.line ?? null,
    evidence: finding.evidence ?? null,
  };
}

/** @param {string} directory @returns {string[]} */
function discoverSkillDirectories(directory) {
  /** @type {string[]} */
  const skills = [];

  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (ignoredDirectories.has(entry.name)) continue;

    const entryPath = resolve(directory, entry.name);
    if (!isWithinRoot(directory, entryPath)) {
      throw new Error(`Skill entry escapes its parent directory: ${entryPath}`);
    }

    const realEntryPath = realpathSync(entryPath);
    if (!isWithinRoot(repositoryRoot, realEntryPath)) {
      throw new Error(`Skill entry escapes the repository: ${entryPath}`);
    }
    if (!statSync(realEntryPath).isDirectory()) continue;

    if (existsSync(join(realEntryPath, "SKILL.md"))) {
      skills.push(entryPath);
    } else if (!entry.isSymbolicLink()) {
      skills.push(...discoverSkillDirectories(entryPath));
    }
  }

  return skills;
}

if (!existsSync(skillsRoot) || !statSync(skillsRoot).isDirectory()) {
  console.error(`Skills directory does not exist: ${skillsRoot}`);
  process.exit(1);
}

const baseline = loadBaseline();
const selfAuditedSkills = new Map();
for (const entry of baseline.selfAuditedSkills) {
  if (selfAuditedSkills.has(entry.skill)) {
    throw new Error(`Duplicate self-audited skill: ${entry.skill}`);
  }
  selfAuditedSkills.set(entry.skill, entry.reason);
}
const discoveredSkillDirectories = discoverSkillDirectories(skillsRoot).sort(
  (left, right) => left.localeCompare(right),
);
const discoveredSkillNames = new Set();
for (const skillPath of discoveredSkillDirectories) {
  const skillName = basename(skillPath);
  if (discoveredSkillNames.has(skillName)) {
    throw new Error(`Duplicate skill name discovered: ${skillName}`);
  }
  discoveredSkillNames.add(skillName);
}

for (const skillName of selfAuditedSkills.keys()) {
  if (!discoveredSkillNames.has(skillName)) {
    throw new Error(`Self-audited skill not found: ${skillName}`);
  }
}

const skillDirectories = discoveredSkillDirectories;
if (skillDirectories.length === 0) {
  console.error(`No skills found under: ${skillsRoot}`);
  process.exit(1);
}

const skillSpecs = skillDirectories.map((skillPath) => {
  /** @type {SkillInfo} */
  const skill = {
    name: basename(skillPath),
    path: skillPath,
    scope: "project",
    agents: ["shared"],
  };
  return {
    skill,
    specResult: validateSkillSpec(skill.path, skill.name),
  };
});
const compatibilityPolicy = evaluateCompatibilityPolicy(
  skillSpecs.map(({ skill, specResult }) => ({
    name: skill.name,
    compatibility: specResult.manifest?.compatibility,
  })),
  baseline.compatibilityOmissions,
);

console.log(
  `Auditing ${skillDirectories.length} skills with the vendored skill-audit CLI`,
);
for (const [skillName, reason] of selfAuditedSkills) {
  console.log(`Self-audited separately: ${skillName} (${reason})`);
}
for (const { skill, reason } of compatibilityPolicy.approvedOmissions) {
  console.log(`Compatibility deliberately omitted: ${skill} (${reason})`);
}
console.log();

const auditedSkills = skillSpecs.map(({ skill, specResult }) => {
  const selfAudited = selfAuditedSkills.has(skill.name);
  const securityResult = selfAudited
    ? { findings: [] }
    : auditSecurity(skill, specResult.manifest);
  const dependencyFindings = selfAudited ? [] : scanDependencies(skill.path);
  return {
    skill,
    specResult,
    securityFindings: securityResult.findings,
    dependencyFindings,
  };
});
const currentFindings = auditedSkills.flatMap(
  ({ skill, specResult, securityFindings, dependencyFindings }) =>
    [...specResult.findings, ...securityFindings, ...dependencyFindings].map(
      (finding) => normalizeFinding(skill, finding),
    ),
);
const acceptedFindingEvaluation = evaluateAcceptedFindings(
  baseline.acceptedFindings,
  currentFindings,
);
const acceptedMatches = new Map(
  acceptedFindingEvaluation.acceptedMatches.map((match) => [
    match.fingerprint,
    match,
  ]),
);

/** @param {SkillInfo} skill @param {Finding[]} findings @returns {Finding[]} */
function removeAcceptedFindings(skill, findings) {
  return findings.filter((finding) => {
    const normalized = normalizeFinding(skill, finding);
    const fingerprint = acceptedFindingFingerprint(normalized);
    const match = acceptedMatches.get(fingerprint);
    if (!match) return true;

    console.log(
      `Accepted finding: ${skill.name}/${finding.id} ${normalized.file}:${normalized.line}`,
    );
    console.log(`  Reason: ${match.acceptedFinding.reason}`);
    return false;
  });
}

const results = auditedSkills.map(
  ({ skill, specResult, securityFindings, dependencyFindings }) => {
    const specFindings = removeAcceptedFindings(skill, specResult.findings);
    const filteredSecurityFindings = removeAcceptedFindings(
      skill,
      securityFindings,
    );
    const filteredDependencyFindings = removeAcceptedFindings(
      skill,
      dependencyFindings,
    );
    const groupedFindings = groupSecurityFindings([
      ...filteredSecurityFindings,
      ...filteredDependencyFindings,
    ]);

    return createGroupedAuditResult(
      skill,
      specResult.manifest,
      specFindings,
      groupedFindings.securityFindings,
      groupedFindings.piiFindings,
      groupedFindings.complianceFindings,
      [],
    );
  },
);

const staleAcceptedFindings = acceptedFindingEvaluation.staleFindings;
if (staleAcceptedFindings.length > 0) {
  console.error("\nStale accepted findings must be reviewed or removed:");
  for (const finding of staleAcceptedFindings) {
    console.error(
      `- ${finding.skill}/${finding.id} ${finding.file} evidence SHA-256 ${String(finding.evidenceSha256)}`,
    );
  }
}

if (acceptedFindingEvaluation.ambiguousMatches.length > 0) {
  console.error("\nAmbiguous accepted findings must be reviewed:");
  for (const match of acceptedFindingEvaluation.ambiguousMatches) {
    const lines = match.actualFindings.map(({ line }) => line).join(", ");
    console.error(
      `- ${match.acceptedFinding.skill}/${match.acceptedFinding.id} ${match.acceptedFinding.file} matched ${match.actualFindings.length} findings at lines ${lines}`,
    );
  }
}

if (compatibilityPolicy.errors.length > 0) {
  console.error("\nSkill compatibility policy errors:");
  for (const error of compatibilityPolicy.errors) {
    console.error(`- ${error}`);
  }
}

const shouldBlock = reportGroupedResults(results, {
  json: false,
  verbose: true,
  threshold: 3,
  mode: "audit",
  block: true,
});

if (
  shouldBlock ||
  staleAcceptedFindings.length > 0 ||
  acceptedFindingEvaluation.ambiguousMatches.length > 0 ||
  compatibilityPolicy.errors.length > 0
) {
  process.exitCode = 1;
}
