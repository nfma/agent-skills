#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const repositoryRoot = fileURLToPath(new URL("..", import.meta.url));
const baselinePath = resolve(repositoryRoot, ".python-quality-baseline.json");
const printBaseline = process.argv.includes("--print-baseline");

/** @param {string} candidate */
function repositoryRelative(candidate) {
  const absoluteCandidate = isAbsolute(candidate)
    ? candidate
    : resolve(repositoryRoot, candidate);
  const relativePath = relative(repositoryRoot, absoluteCandidate);
  if (
    relativePath === ".." ||
    relativePath.startsWith(`..${sep}`) ||
    isAbsolute(relativePath)
  ) {
    throw new Error(
      `Tool reported a path outside the repository: ${candidate}`,
    );
  }
  return relativePath.split(sep).join("/");
}

/** @param {unknown} value */
function oneLine(value) {
  return String(value).replaceAll(/\s+/g, " ").trim();
}

/**
 * @param {string} command
 * @param {string[]} args
 * @returns {string}
 */
function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: repositoryRoot,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0 && result.status !== 1) {
    throw new Error(
      `${command} ${args.join(" ")} failed with exit ${result.status}\n${result.stderr}`,
    );
  }
  return result.stdout;
}

/** @param {string[]} args */
function runUv(args) {
  return run("uv", ["run", "--frozen", ...args]);
}

/** @param {string} value */
function parseJson(value) {
  return /** @type {unknown} */ (JSON.parse(value));
}

function collectRuff() {
  const output = parseJson(
    runUv(["ruff", "check", "skills", "tests/python", "--output-format=json"]),
  );
  if (!Array.isArray(output)) throw new Error("Unexpected Ruff JSON output");
  return output.map((finding) => {
    const item = /** @type {Record<string, any>} */ (finding);
    return `${repositoryRelative(item.filename)}:${item.location.row}:${item.location.column}:${item.code}:${oneLine(item.message)}`;
  });
}

function collectRuffFormat() {
  const output = parseJson(
    runUv([
      "ruff",
      "format",
      "--check",
      "skills",
      "tests/python",
      "--output-format=json",
    ]),
  );
  if (!Array.isArray(output)) {
    throw new Error("Unexpected Ruff format JSON output");
  }
  return output.map((finding) => {
    const item = /** @type {Record<string, any>} */ (finding);
    return `${repositoryRelative(item.filename)}:${item.location.row}:${item.location.column}:${item.code}`;
  });
}

function collectPyright() {
  const output = /** @type {Record<string, any>} */ (
    parseJson(runUv(["pyright", "--outputjson"]))
  );
  if (!Array.isArray(output.generalDiagnostics)) {
    throw new Error("Unexpected Pyright JSON output");
  }
  return output.generalDiagnostics.map((finding) => {
    const item = /** @type {Record<string, any>} */ (finding);
    return `${repositoryRelative(item.file)}:${item.range.start.line + 1}:${item.range.start.character + 1}:${item.severity}:${item.rule ?? ""}:${oneLine(item.message)}`;
  });
}

function collectMypy() {
  const output = runUv([
    "mypy",
    "skills",
    "tests/python",
    "--output=json",
  ]).trim();
  if (!output) return [];
  return output.split("\n").map((line) => {
    const item = /** @type {Record<string, any>} */ (JSON.parse(line));
    return `${repositoryRelative(item.file)}:${item.line}:${item.column}:${item.severity}:${item.code ?? ""}:${oneLine(item.message)}`;
  });
}

function collectBandit() {
  const output = /** @type {Record<string, any>} */ (
    parseJson(
      runUv(["bandit", "-r", "skills", "tests/python", "-f", "json", "-q"]),
    )
  );
  if (!Array.isArray(output.results)) {
    throw new Error("Unexpected Bandit JSON output");
  }
  return output.results.map((finding) => {
    const item = /** @type {Record<string, any>} */ (finding);
    return `${repositoryRelative(item.filename)}:${item.line_number}:${item.col_offset + 1}:${item.issue_severity}:${item.issue_confidence}:${item.test_id}:${oneLine(item.issue_text)}`;
  });
}

/** @param {string[]} values */
function uniqueSorted(values) {
  return [...new Set(values)].sort();
}

const current = {
  bandit: uniqueSorted(collectBandit()),
  mypy: uniqueSorted(collectMypy()),
  pyright: uniqueSorted(collectPyright()),
  ruff: uniqueSorted(collectRuff()),
  ruffFormat: uniqueSorted(collectRuffFormat()),
};
const reviewNotes = {
  bandit:
    "Imported examples contain reviewed subprocess and URL-use patterns; exact locations still gate all changes.",
  pyright:
    "One imported community-evals optional-path diagnostic is retained as exact existing debt.",
  ruff: "Imported examples retain upstream lint debt; first-party Python remains clean.",
  ruffFormat:
    "Imported examples retain upstream formatting; exact diff locations gate all changes.",
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
    // eslint-disable-next-line security/detect-non-literal-fs-filename
    JSON.parse(readFileSync(baselinePath, "utf8"))
  );
if (baseline.version !== 1) {
  throw new Error(
    `Unsupported Python quality baseline version: ${baseline.version}`,
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
    "\nReview the changes, then regenerate with: node scripts/check-python-quality.mjs --print-baseline",
  );
  process.exitCode = 1;
}
