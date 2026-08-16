#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = fileURLToPath(new URL("..", import.meta.url));
const vendorRoot = resolve(repositoryRoot, "vendor/skill-audit/skill-audit");
const baselinePath = resolve(repositoryRoot, ".shell-quality-baseline.json");
const printBaseline = process.argv.includes("--print-baseline");

/**
 * @param {string} command
 * @param {string[]} args
 * @param {{ cwd?: string, acceptedStatuses?: number[] }} [options]
 */
function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? repositoryRoot,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  const acceptedStatuses = options.acceptedStatuses ?? [0];
  if (!acceptedStatuses.includes(result.status ?? -1)) {
    throw new Error(
      `${command} ${args.join(" ")} failed with exit ${result.status}\n${result.stdout}${result.stderr}`,
    );
  }
  return result.stdout;
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

/** @param {string} candidate */
function checkedRepositoryPath(candidate) {
  const lexicalPath = resolve(repositoryRoot, candidate);
  if (!isWithinRoot(repositoryRoot, lexicalPath)) {
    throw new Error(`Shell path escapes the repository: ${candidate}`);
  }
  const canonicalPath = realpathSync(lexicalPath);
  if (!isWithinRoot(repositoryRoot, canonicalPath)) {
    throw new Error(`Shell path resolves outside the repository: ${candidate}`);
  }
  return candidate.split(sep).join("/");
}

/** @param {string} root @param {string} prefix */
function trackedShellFiles(root, prefix = "") {
  return run(
    "git",
    [
      "ls-files",
      "-z",
      "--",
      ":(glob)**/*.sh",
      ":(glob)**/*.bash",
      ":(glob)**/*.bats",
    ],
    { cwd: root },
  )
    .split("\0")
    .filter(Boolean)
    .map((file) => checkedRepositoryPath(`${prefix}${file}`));
}

const shellFiles = [
  ...trackedShellFiles(repositoryRoot),
  ...trackedShellFiles(vendorRoot, "vendor/skill-audit/skill-audit/"),
].sort();

if (shellFiles.length === 0) throw new Error("No tracked shell files found");

for (const file of shellFiles) {
  if (file.endsWith(".bats")) continue;
  const absolutePath = resolve(repositoryRoot, file);
  const firstLine = readFileSync(absolutePath, "utf8").split("\n", 1)[0] ?? "";
  const shell =
    file.endsWith(".bash") || firstLine.includes("bash") ? "bash" : "sh";
  run(shell, ["-n", absolutePath]);
}

const shellcheckOutput =
  /** @type {{ comments: Array<Record<string, any>> }} */ (
    JSON.parse(
      run(
        "shellcheck",
        [
          "-f",
          "json1",
          "--",
          ...shellFiles.filter((file) => !file.endsWith(".bats")),
        ],
        { acceptedStatuses: [0, 1] },
      ),
    )
  );
const shellcheck = shellcheckOutput.comments.map(
  (finding) =>
    `${checkedRepositoryPath(finding.file)}:${finding.line}:${finding.column}:SC${finding.code}:${finding.level}:${finding.message}`,
);

const shfmt = [];
for (const file of shellFiles) {
  const language = file.endsWith(".bats") ? "bats" : "auto";
  const diff = run(
    "shfmt",
    ["-d", "-ln", language, "-i", "2", "-ci", "-bn", file],
    { acceptedStatuses: [0, 1] },
  );
  if (!diff) continue;
  const digest = createHash("sha256").update(diff).digest("hex");
  shfmt.push(`${file}:${digest}`);
}

/** @param {string[]} findings */
function uniqueSorted(findings) {
  return [...new Set(findings)].sort();
}

const current = {
  shellcheck: uniqueSorted(shellcheck),
  shfmt: uniqueSorted(shfmt),
};
const reviewNotes = {
  shellcheck:
    "Only imported Hugging Face examples and vendored skill-audit scripts are accepted; exact locations gate changes.",
  shfmt:
    "Imported and vendored formatting is preserved; SHA-256 diff fingerprints gate changes.",
};

if (printBaseline) {
  console.log(
    JSON.stringify(
      { version: 1, reviewNotes, acceptedFindings: current },
      null,
      2,
    ),
  );
  process.exit(0);
}

const baseline =
  /** @type {{ version: number, reviewNotes?: Record<string, string>, acceptedFindings: Record<string, string[]> }} */ (
    // The path is fixed to the repository root and never accepts user input.
    JSON.parse(readFileSync(baselinePath, "utf8"))
  );
if (baseline.version !== 1) {
  throw new Error(
    `Unsupported shell quality baseline version: ${baseline.version}`,
  );
}

let failed = false;
for (const [tool, findings] of Object.entries(current)) {
  const accepted = new Set(baseline.acceptedFindings[tool] ?? []);
  const actual = new Set(findings);
  const newFindings = findings.filter((finding) => !accepted.has(finding));
  const staleFindings = [...accepted].filter((finding) => !actual.has(finding));

  console.log(`${tool}: ${findings.length} accepted finding(s)`);
  if (newFindings.length > 0) {
    failed = true;
    console.error(`\n${tool}: new findings`);
    for (const finding of newFindings) console.error(`- ${finding}`);
  }
  if (staleFindings.length > 0) {
    failed = true;
    console.error(`\n${tool}: stale baseline findings`);
    for (const finding of staleFindings) console.error(`- ${finding}`);
  }
}

if (failed) {
  console.error(
    "\nReview the changes, then regenerate with: node scripts/check-shell-quality.mjs --print-baseline",
  );
  process.exitCode = 1;
}
