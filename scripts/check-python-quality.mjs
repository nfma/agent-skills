#!/usr/bin/env node

import { isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { checkQualityBaseline, uniqueSorted } from "./quality-baseline.mjs";

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
  if (!Array.isArray(output)) {
    throw new TypeError("Unexpected Ruff JSON output");
  }
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
    throw new TypeError("Unexpected Ruff format JSON output");
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
    throw new TypeError("Unexpected Pyright JSON output");
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
    throw new TypeError("Unexpected Bandit JSON output");
  }
  return output.results.map((finding) => {
    const item = /** @type {Record<string, any>} */ (finding);
    return `${repositoryRelative(item.filename)}:${item.line_number}:${item.col_offset + 1}:${item.issue_severity}:${item.issue_confidence}:${item.test_id}:${oneLine(item.issue_text)}`;
  });
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

checkQualityBaseline({
  baselinePath,
  current,
  label: "Python",
  printBaseline,
  regenerationCommand: "node scripts/check-python-quality.mjs --print-baseline",
  reviewNotes,
});
