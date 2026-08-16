#!/usr/bin/env node

/* eslint-disable security/detect-non-literal-fs-filename -- Every path is repository-derived, descriptor-pinned, or confined to a fresh temporary directory. */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, posix, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const PIN_SHA256 =
  "5cf3f5f9cd5bc9a4a37c19876c71ffe7362f6170908ae9559af7ea01c5ef232a";
export const LEGACY_SUBMODULE_COMMIT =
  "594734decf04b32bdf54a8d6587dc6abed372807";
export const LEGACY_PATCH_SHA256 =
  "da27d3a04e4ebca5c96e7ce0165417fe1dd1f6efc8bc1b0099a9917a25334038";

const repositoryRoot = fileURLToPath(new URL("..", import.meta.url));
const pinPath = resolve(repositoryRoot, ".skill-audit-release.json");
const documentationRoot = resolve(repositoryRoot, "skills/skill-audit");
const legacyVendorRoot = resolve(repositoryRoot, "vendor/skill-audit");
const executablePath = resolve(
  repositoryRoot,
  "vendor/skill-audit/dist/skill-audit.mjs",
);
const fixedCorpusRoot = resolve(
  repositoryRoot,
  "tests/fixtures/skill-audit-release",
);
const utf8Decoder = new TextDecoder("utf-8", { fatal: true });

/** @typedef {{ path: string, sha256: string }} DocumentationFile */
/**
 * @typedef {{
 *   schemaVersion: 1,
 *   version: string,
 *   tag: string,
 *   sourceRepository: string,
 *   sourceCommit: string,
 *   buildWorkflow: string,
 *   minimumNode: string,
 *   executable: {
 *     name: string,
 *     sha256: string,
 *     sizeBytes: number,
 *     exports: string[],
 *     embeddedRulesSha256: string,
 *   },
 *   documentation: {
 *     files: DocumentationFile[],
 *     upstreamDocsSha256: string,
 *   },
 * }} ReleaseDescriptor
 */

/** @param {string | Uint8Array} value */
export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

/** @param {string} path */
function readRegularFile(path) {
  const metadata = lstatSync(path);
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    throw new Error(`Expected a regular file, not a symlink: ${path}`);
  }
  return readFileSync(path);
}

/** @param {string} version */
function semverParts(version) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) {
    throw new Error(`Expected a plain semantic version: ${version}`);
  }
  return version.split(".").map(Number);
}

/** @param {string} actual @param {string} minimum */
function isVersionAtLeast(actual, minimum) {
  const left = semverParts(actual);
  const right = semverParts(minimum);
  for (let index = 0; index < left.length; index += 1) {
    const leftPart = left[index] ?? 0;
    const rightPart = right[index] ?? 0;
    if (leftPart !== rightPart) return leftPart > rightPart;
  }
  return true;
}

/** @param {string} command @param {string[]} args @param {{ cwd?: string, encoding?: BufferEncoding | null, env?: NodeJS.ProcessEnv }} [options] */
function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? repositoryRoot,
    encoding: options.encoding === undefined ? "utf8" : options.encoding,
    env: options.env ?? process.env,
    maxBuffer: 20 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  return result;
}

/** @param {string} label @param {ReturnType<typeof spawnSync>} result */
function requireSuccess(label, result) {
  if (result.status !== 0) {
    const stdout = Buffer.isBuffer(result.stdout)
      ? result.stdout.toString("utf8")
      : (result.stdout ?? "");
    const stderr = Buffer.isBuffer(result.stderr)
      ? result.stderr.toString("utf8")
      : (result.stderr ?? "");
    throw new Error(
      `${label} failed with exit ${result.status}\n${stdout}${stderr}`,
    );
  }
}

/** @param {string | Buffer | null | undefined} output */
function outputText(output) {
  return Buffer.isBuffer(output) ? output.toString("utf8") : (output ?? "");
}

/** @param {unknown} value */
function displayValue(value) {
  const encoded = JSON.stringify(value);
  return encoded === undefined ? String(value) : encoded;
}

/** @param {Record<string, any>} descriptor */
function assertReleaseIdentity(descriptor) {
  if (descriptor.schemaVersion !== 1) {
    throw new Error(
      `skill-audit release descriptor schemaVersion mismatch: expected 1, got ${displayValue(descriptor.schemaVersion)}`,
    );
  }
  if (descriptor.sourceRepository !== "nfma/skill-audit") {
    throw new Error(
      `skill-audit release descriptor sourceRepository mismatch: expected "nfma/skill-audit", got ${displayValue(descriptor.sourceRepository)}`,
    );
  }
  const expectedTag = `v${descriptor.version}`;
  if (descriptor.tag !== expectedTag) {
    throw new Error(
      `skill-audit release descriptor tag mismatch: expected ${displayValue(expectedTag)}, got ${displayValue(descriptor.tag)}`,
    );
  }
  const expectedWorkflow = `.github/workflows/release.yml@${descriptor.sourceCommit}`;
  if (descriptor.buildWorkflow !== expectedWorkflow) {
    throw new Error(
      `skill-audit release descriptor buildWorkflow mismatch: expected ${displayValue(expectedWorkflow)}, got ${displayValue(descriptor.buildWorkflow)}`,
    );
  }
  if (!/^[0-9a-f]{40}$/.test(descriptor.sourceCommit ?? "")) {
    throw new Error(
      `skill-audit release descriptor sourceCommit mismatch: expected 40 lowercase hexadecimal characters, got ${displayValue(descriptor.sourceCommit)}`,
    );
  }
  if (!/^\d+\.\d+\.\d+$/.test(descriptor.minimumNode ?? "")) {
    throw new Error(
      `skill-audit release descriptor minimumNode mismatch: expected a plain semantic version, got ${displayValue(descriptor.minimumNode)}`,
    );
  }
  if (!isVersionAtLeast(process.versions.node, descriptor.minimumNode)) {
    throw new Error(
      `skill-audit Node version mismatch: expected at least ${descriptor.minimumNode}, got ${process.versions.node}`,
    );
  }
}

/** @param {Record<string, any>} descriptor */
function assertExecutableIdentity(descriptor) {
  const executable = descriptor.executable;
  if (!executable || typeof executable !== "object") {
    throw new Error(
      `skill-audit executable descriptor type mismatch: expected non-null object, got ${executable === null ? "null" : typeof executable}`,
    );
  }
  const expectedExecutableName = `skill-audit-v${descriptor.version}.mjs`;
  if (executable.name !== expectedExecutableName) {
    throw new Error(
      `skill-audit executable name mismatch: expected ${displayValue(expectedExecutableName)}, got ${displayValue(executable.name)}`,
    );
  }
  if (!/^[0-9a-f]{64}$/.test(executable.sha256 ?? "")) {
    throw new Error(
      `skill-audit executable sha256 mismatch: expected 64 lowercase hexadecimal characters, got ${displayValue(executable.sha256)}`,
    );
  }
  if (!/^[0-9a-f]{64}$/.test(executable.embeddedRulesSha256 ?? "")) {
    throw new Error(
      `skill-audit executable embeddedRulesSha256 mismatch: expected 64 lowercase hexadecimal characters, got ${displayValue(executable.embeddedRulesSha256)}`,
    );
  }
  if (
    !Number.isSafeInteger(executable.sizeBytes) ||
    executable.sizeBytes <= 0
  ) {
    throw new Error(
      `skill-audit executable sizeBytes mismatch: expected a positive safe integer, got ${displayValue(executable.sizeBytes)}`,
    );
  }
  if (!Array.isArray(executable.exports)) {
    throw new Error(
      `skill-audit executable exports mismatch: expected an array, got ${displayValue(executable.exports)}`,
    );
  }
  if (executable.exports.length !== 6) {
    throw new Error(
      `skill-audit executable exports length mismatch: expected 6, got ${executable.exports.length}`,
    );
  }
}

/** @param {Record<string, any>} descriptor */
function assertDocumentationIdentity(descriptor) {
  const documentation = descriptor.documentation;
  if (!documentation || typeof documentation !== "object") {
    throw new Error(
      `skill-audit documentation descriptor type mismatch: expected non-null object, got ${documentation === null ? "null" : typeof documentation}`,
    );
  }
  if (!Array.isArray(documentation.files)) {
    throw new Error(
      `skill-audit documentation files mismatch: expected an array, got ${displayValue(documentation.files)}`,
    );
  }
  if (documentation.files.length !== 6) {
    throw new Error(
      `skill-audit documentation files length mismatch: expected 6, got ${documentation.files.length}`,
    );
  }
  if (!/^[0-9a-f]{64}$/.test(documentation.upstreamDocsSha256 ?? "")) {
    throw new Error(
      `skill-audit documentation upstreamDocsSha256 mismatch: expected 64 lowercase hexadecimal characters, got ${displayValue(documentation.upstreamDocsSha256)}`,
    );
  }
}

/** @param {unknown} value @returns {asserts value is ReleaseDescriptor} */
function assertDescriptor(value) {
  if (!value || typeof value !== "object") {
    throw new Error(
      `skill-audit release descriptor type mismatch: expected non-null object, got ${value === null ? "null" : typeof value}`,
    );
  }
  const descriptor = /** @type {Record<string, any>} */ (value);
  assertReleaseIdentity(descriptor);
  assertExecutableIdentity(descriptor);
  assertDocumentationIdentity(descriptor);
}

/** @param {string} [path] */
export function loadPinnedDescriptor(path = pinPath) {
  const bytes = readRegularFile(path);
  const digest = sha256(bytes);
  if (digest !== PIN_SHA256) {
    throw new Error(
      `skill-audit release pin digest mismatch: expected ${PIN_SHA256}, got ${digest}`,
    );
  }
  const descriptor = JSON.parse(utf8Decoder.decode(bytes));
  assertDescriptor(descriptor);
  return { bytes, descriptor };
}

/** @param {string} root */
function documentationPaths(root) {
  /** @type {string[]} */
  const paths = [];
  /** @param {string} directory @param {string} relativeDirectory */
  const visit = (directory, relativeDirectory) => {
    const metadata = lstatSync(directory);
    if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
      throw new Error(
        `Documentation directory must not be a symlink: ${directory}`,
      );
    }
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const absolutePath = join(directory, entry.name);
      const relativePath = relativeDirectory
        ? posix.join(relativeDirectory, entry.name)
        : entry.name;
      const entryMetadata = lstatSync(absolutePath);
      if (entryMetadata.isSymbolicLink()) {
        throw new Error(
          `Documentation path must not be a symlink: ${relativePath}`,
        );
      }
      if (entryMetadata.isDirectory()) {
        visit(absolutePath, relativePath);
      } else if (entryMetadata.isFile()) {
        paths.push(relativePath);
      } else {
        throw new Error(
          `Documentation path must be a regular file: ${relativePath}`,
        );
      }
    }
  };
  visit(root, "");
  return paths.sort((left, right) =>
    Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8")),
  );
}

/** @param {ReleaseDescriptor} descriptor @param {string} [root] */
export function verifyDocumentation(descriptor, root = documentationRoot) {
  const expectedPaths = descriptor.documentation.files.map(({ path }) => path);
  const actualPaths = documentationPaths(root);
  if (JSON.stringify(actualPaths) !== JSON.stringify(expectedPaths)) {
    throw new Error(
      `skill-audit documentation paths differ: expected ${expectedPaths.join(", ")}; got ${actualPaths.join(", ")}`,
    );
  }

  const files = descriptor.documentation.files.map(
    ({ path, sha256: expected }) => {
      const bytes = readRegularFile(join(root, ...path.split("/")));
      const content = utf8Decoder.decode(bytes);
      if (content.includes("\r")) {
        throw new Error(
          `skill-audit documentation is not byte-normalized: ${path}`,
        );
      }
      const actual = sha256(content);
      if (actual !== expected) {
        throw new Error(
          `skill-audit documentation digest mismatch for ${path}: expected ${expected}, got ${actual}`,
        );
      }
      return { path, sha256: actual };
    },
  );

  const aggregate = files
    .map(({ path, sha256: digest }) => `${path}\0${digest}\n`)
    .join("");
  const consumerDocsSha256 = sha256(aggregate);
  if (consumerDocsSha256 !== descriptor.documentation.upstreamDocsSha256) {
    throw new Error(
      `skill-audit documentation aggregate mismatch: expected ${descriptor.documentation.upstreamDocsSha256}, got ${consumerDocsSha256}`,
    );
  }
  return consumerDocsSha256;
}

/** @param {string} executable @param {string} expectedDigest */
function verifyEmbeddedRules(executable, expectedDigest) {
  const source = utf8Decoder.decode(readRegularFile(executable));
  const markerIndex = source.indexOf(`"${expectedDigest}"`);
  if (markerIndex === -1) {
    throw new Error(
      `skill-audit embedded rules digest marker mismatch: expected ${displayValue(expectedDigest)}, got absent`,
    );
  }
  const encodedMatch = source
    .slice(markerIndex)
    // The input is the size- and digest-pinned release executable.
    // eslint-disable-next-line security/detect-unsafe-regex
    .match(/,[$A-Za-z_][$\w]*=(\[(?:"[A-Za-z0-9+/=]*",?)+\])\.join\(""\)/);
  if (!encodedMatch) {
    throw new Error(
      `skill-audit embedded rules payload mismatch: expected encoded chunks after digest ${expectedDigest}, got absent`,
    );
  }
  const encodedChunks = encodedMatch[1];
  if (!encodedChunks) {
    throw new Error(
      `skill-audit embedded rules payload mismatch: expected non-empty encoded chunks, got ${displayValue(encodedChunks)}`,
    );
  }
  const chunks = /** @type {string[]} */ (JSON.parse(encodedChunks));
  const encoded = chunks.join("");
  const decoded = Buffer.from(encoded, "base64");
  const canonicalEncoded = decoded.toString("base64");
  if (canonicalEncoded !== encoded) {
    throw new Error(
      `skill-audit embedded rules base64 mismatch: expected canonical length ${canonicalEncoded.length} and digest ${sha256(canonicalEncoded)}, got length ${encoded.length} and digest ${sha256(encoded)}`,
    );
  }
  utf8Decoder.decode(decoded);
  const actualDigest = sha256(decoded);
  if (actualDigest !== expectedDigest) {
    throw new Error(
      `skill-audit embedded rules digest mismatch: expected ${expectedDigest}, got ${actualDigest}`,
    );
  }
}

const importProbe = String.raw`
import childProcess from "node:child_process";
import dns from "node:dns";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import tls from "node:tls";
import { syncBuiltinESMExports } from "node:module";

const blocked = (operation) => () => {
  throw new Error("Import attempted prohibited side effect: " + operation);
};
globalThis.fetch = blocked("fetch");
for (const [module, methods] of [
  [childProcess, ["exec", "execFile", "execFileSync", "execSync", "fork", "spawn", "spawnSync"]],
  [dns, ["lookup", "resolve", "resolve4", "resolve6", "reverse"]],
  [http, ["get", "request"]],
  [https, ["get", "request"]],
  [net, ["connect", "createConnection"]],
  [tls, ["connect"]],
]) {
  for (const method of methods) module[method] = blocked(method);
}
process.exit = blocked("process.exit");
process.kill = blocked("process.kill");
process.abort = blocked("process.abort");
if (process.reallyExit) process.reallyExit = blocked("process.reallyExit");
if (globalThis.WebSocket) globalThis.WebSocket = class BlockedWebSocket {
  constructor() {
    throw new Error("Import attempted prohibited side effect: WebSocket");
  }
};
syncBuiltinESMExports();
const before = fs.readdirSync(process.cwd()).sort();
const loaded = await import(process.argv[1]);
const after = fs.readdirSync(process.cwd()).sort();
if (JSON.stringify(before) !== JSON.stringify(after)) {
  throw new Error("Import changed the probe directory");
}
console.log(JSON.stringify(Object.keys(loaded).sort()));
`;

/** @param {ReleaseDescriptor} descriptor @param {string} [path] */
export function verifyExecutable(descriptor, path = executablePath) {
  const metadata = lstatSync(path);
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    throw new Error(`skill-audit executable must be a regular file: ${path}`);
  }
  const bytes = readFileSync(path);
  const digest = sha256(bytes);
  if (
    digest !== descriptor.executable.sha256 ||
    metadata.size !== descriptor.executable.sizeBytes
  ) {
    throw new Error(
      `skill-audit executable identity mismatch: expected ${descriptor.executable.sha256}/${descriptor.executable.sizeBytes}, got ${digest}/${metadata.size}`,
    );
  }
  verifyEmbeddedRules(path, descriptor.executable.embeddedRulesSha256);

  const syntax = run(process.execPath, ["--check", path]);
  requireSuccess("skill-audit syntax check", syntax);
  const syntaxStdout = outputText(syntax.stdout);
  const syntaxStderr = outputText(syntax.stderr);
  if (syntaxStdout !== "" || syntaxStderr !== "") {
    throw new Error(
      `skill-audit syntax output mismatch: expected empty stdout/stderr, got stdout ${displayValue(syntaxStdout)} and stderr ${displayValue(syntaxStderr)}`,
    );
  }

  const version = run(process.execPath, [path, "--version"]);
  requireSuccess("skill-audit version check", version);
  const versionStdout = outputText(version.stdout).trim();
  if (
    versionStdout !== descriptor.version ||
    outputText(version.stderr) !== ""
  ) {
    throw new Error(
      `skill-audit version mismatch: expected ${descriptor.version}, got ${versionStdout}`,
    );
  }

  const probeRoot = mkdtempSync(join(dirname(path), ".import-probe-"));
  try {
    const executableDirectory = dirname(path);
    const probeName = probeRoot.slice(executableDirectory.length + 1);
    const directoryBefore = readdirSync(executableDirectory)
      .filter((entry) => entry !== probeName)
      .sort();
    const moduleUrl = pathToFileURL(path).href;
    const probe = run(
      process.execPath,
      ["--input-type=module", "--eval", importProbe, moduleUrl],
      {
        cwd: probeRoot,
        env: {
          ...process.env,
          HOME: probeRoot,
          XDG_CACHE_HOME: probeRoot,
          XDG_CONFIG_HOME: probeRoot,
          XDG_DATA_HOME: probeRoot,
        },
      },
    );
    requireSuccess("skill-audit side-effect-free import probe", probe);
    const expectedExports = JSON.stringify(
      [...descriptor.executable.exports].sort(),
    );
    const probeStdout = outputText(probe.stdout).trim();
    if (probeStdout !== expectedExports || outputText(probe.stderr) !== "") {
      throw new Error(
        `skill-audit export contract mismatch: expected ${expectedExports}, got ${probeStdout}`,
      );
    }
    const remainingProbeFiles = readdirSync(probeRoot).sort();
    if (remainingProbeFiles.length !== 0) {
      throw new Error(
        `skill-audit import probe directory mismatch: expected [], got ${displayValue(remainingProbeFiles)}`,
      );
    }
    const directoryAfter = readdirSync(executableDirectory)
      .filter((entry) => entry !== probeName)
      .sort();
    if (JSON.stringify(directoryBefore) !== JSON.stringify(directoryAfter)) {
      throw new Error(
        `skill-audit executable directory mismatch after import: expected ${displayValue(directoryBefore)}, got ${displayValue(directoryAfter)}`,
      );
    }
    const digestAfterImport = sha256(readFileSync(path));
    if (digestAfterImport !== descriptor.executable.sha256) {
      throw new Error(
        `skill-audit executable digest mismatch after import: expected ${descriptor.executable.sha256}, got ${digestAfterImport}`,
      );
    }
  } finally {
    rmSync(probeRoot, { recursive: true, force: true });
  }
}

/** @param {string} executable @param {string} fixtureRoot */
export function verifyFixedCorpus(executable, fixtureRoot) {
  const probe = String.raw`
const loaded = await import(process.argv[1]);
const reports = [];
for (const fixture of process.argv.slice(2)) {
  const spec = loaded.validateSkillSpec(fixture, "fixture");
  const skill = { name: "fixture", path: fixture, scope: "project", agents: ["shared"] };
  const security = loaded.auditSecurity(skill, spec.manifest);
  const grouped = loaded.groupSecurityFindings(security.findings);
  const result = loaded.createGroupedAuditResult(
    skill,
    spec.manifest,
    spec.findings,
    grouped.securityFindings,
    grouped.piiFindings,
    grouped.complianceFindings,
    [],
  );
  reports.push({
    findings: spec.findings.map((finding) => finding.id),
    security: security.findings.map((finding) => finding.id),
    riskLevel: result.riskLevel,
    riskScore: result.riskScore,
  });
}
console.log(JSON.stringify(reports));
`;
  const fixtures = [join(fixtureRoot, "legacy"), join(fixtureRoot, "portable")];
  const result = run(process.execPath, [
    "--input-type=module",
    "--eval",
    probe,
    pathToFileURL(executable).href,
    ...fixtures,
  ]);
  requireSuccess("skill-audit fixed-corpus probe", result);
  const expected = JSON.stringify([
    { findings: [], security: [], riskLevel: "safe", riskScore: 0 },
    { findings: [], security: [], riskLevel: "safe", riskScore: 0 },
  ]);
  const resultStdout = outputText(result.stdout).trim();
  if (resultStdout !== expected || outputText(result.stderr) !== "") {
    throw new Error(
      `skill-audit fixed-corpus mismatch: expected ${expected}, got ${resultStdout}`,
    );
  }
}

/** @param {string} [root] @param {{ expectedCommit?: string, acceptedPatchSha256?: string }} [options] */
export function cleanupLegacyVendor(root = legacyVendorRoot, options = {}) {
  const gitMetadata = join(root, ".git");
  try {
    lstatSync(gitMetadata);
  } catch (error) {
    if (/** @type {NodeJS.ErrnoException} */ (error).code === "ENOENT")
      return false;
    throw error;
  }

  const expectedCommit = options.expectedCommit ?? LEGACY_SUBMODULE_COMMIT;
  const acceptedPatchSha256 =
    options.acceptedPatchSha256 ?? LEGACY_PATCH_SHA256;
  const head = run("git", ["rev-parse", "HEAD"], { cwd: root });
  requireSuccess("legacy skill-audit HEAD check", head);
  const actualHead = outputText(head.stdout).trim();
  if (actualHead !== expectedCommit) {
    throw new Error(
      `Refusing to remove legacy skill-audit at unexpected commit ${actualHead}`,
    );
  }

  const untracked = run(
    "git",
    ["ls-files", "--others", "--exclude-standard", "-z"],
    { cwd: root, encoding: null },
  );
  requireSuccess("legacy skill-audit untracked-file check", untracked);
  if (untracked.stdout.length !== 0) {
    const untrackedPaths = outputText(untracked.stdout)
      .split("\0")
      .filter(Boolean);
    throw new Error(
      `Refusing to remove legacy skill-audit: expected no untracked files, got ${displayValue(untrackedPaths)}`,
    );
  }

  const diff = run("git", ["diff", "--no-ext-diff", "--binary"], {
    cwd: root,
    encoding: null,
  });
  requireSuccess("legacy skill-audit patch check", diff);
  const staged = run("git", ["diff", "--cached", "--quiet"], { cwd: root });
  if (staged.status !== 0) {
    throw new Error(
      `Refusing to remove legacy skill-audit with staged changes: expected staged diff exit 0, got ${displayValue(staged.status)}`,
    );
  }
  const patchDigest = sha256(diff.stdout);
  if (diff.stdout.length !== 0 && patchDigest !== acceptedPatchSha256) {
    throw new Error(
      `Refusing to remove legacy skill-audit with unapproved patch ${patchDigest}`,
    );
  }

  rmSync(root, { recursive: true, force: false });
  console.log(
    diff.stdout.length === 0
      ? `Removed clean legacy skill-audit checkout at ${expectedCommit}`
      : `Removed legacy skill-audit checkout with approved patch ${acceptedPatchSha256}`,
  );
  return true;
}

/** @param {ReleaseDescriptor} descriptor */
function releaseDownloadUrl(descriptor) {
  return `https://github.com/${descriptor.sourceRepository}/releases/download/${descriptor.tag}/${descriptor.executable.name}`;
}

/** @param {Response} response @param {number} expectedSize */
export async function readBoundedResponse(response, expectedSize) {
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null && Number(contentLength) !== expectedSize) {
    throw new Error(
      `skill-audit download size mismatch: expected ${expectedSize}, got ${contentLength}`,
    );
  }
  if (!response.body) {
    throw new Error("skill-audit download returned no response body");
  }

  const chunks = [];
  let total = 0;
  for await (const chunk of response.body) {
    total += chunk.byteLength;
    if (total > expectedSize) {
      throw new Error(
        `skill-audit download exceeded the pinned size ${expectedSize}`,
      );
    }
    chunks.push(Buffer.from(chunk));
  }
  if (total !== expectedSize) {
    throw new Error(
      `skill-audit download size mismatch: expected ${expectedSize}, got ${total}`,
    );
  }
  return Buffer.concat(chunks, total);
}

/**
 * @param {ReleaseDescriptor} descriptor
 * @param {{ executable?: string, corpusRoot?: string, fetchImpl?: typeof fetch }} [options]
 */
export async function installExecutable(descriptor, options = {}) {
  const installedExecutable = options.executable ?? executablePath;
  const corpusRoot = options.corpusRoot ?? fixedCorpusRoot;
  const fetchImpl = options.fetchImpl ?? fetch;
  try {
    verifyExecutable(descriptor, installedExecutable);
    console.log(`Verified installed skill-audit ${descriptor.version}`);
    return;
  } catch (error) {
    try {
      lstatSync(installedExecutable);
      console.warn(
        `Installed skill-audit failed verification: ${error instanceof Error ? error.message : String(error)}. Downloading a verified replacement.`,
      );
    } catch (statError) {
      if (/** @type {NodeJS.ErrnoException} */ (statError).code !== "ENOENT") {
        throw statError;
      }
    }
  }

  const destinationDirectory = dirname(installedExecutable);
  mkdirSync(destinationDirectory, { recursive: true, mode: 0o755 });
  if (realpathSync(destinationDirectory) !== destinationDirectory) {
    throw new Error(
      `skill-audit destination must not traverse a symlink: ${destinationDirectory}`,
    );
  }
  const temporaryDirectory = mkdtempSync(
    join(destinationDirectory, ".skill-audit-download-"),
  );
  const temporaryPath = join(temporaryDirectory, descriptor.executable.name);
  try {
    const response = await fetchImpl(releaseDownloadUrl(descriptor), {
      redirect: "follow",
    });
    if (!response.ok) {
      throw new Error(
        `skill-audit download failed with HTTP ${response.status} ${response.statusText}`,
      );
    }
    const bytes = await readBoundedResponse(
      response,
      descriptor.executable.sizeBytes,
    );
    writeFileSync(temporaryPath, bytes, { flag: "wx", mode: 0o600 });
    verifyExecutable(descriptor, temporaryPath);
    verifyFixedCorpus(temporaryPath, corpusRoot);
    chmodSync(temporaryPath, 0o755);
    renameSync(temporaryPath, installedExecutable);
    console.log(
      `Installed skill-audit ${descriptor.version} from ${releaseDownloadUrl(descriptor)}`,
    );
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
}

/** @param {string} releaseDescriptorPath */
export function verifyReleaseDescriptorBytes(releaseDescriptorPath) {
  const tracked = loadPinnedDescriptor();
  const released = readRegularFile(releaseDescriptorPath);
  if (!tracked.bytes.equals(released)) {
    throw new Error(
      `Tracked skill-audit pin digest/size mismatch: expected ${sha256(tracked.bytes)}/${tracked.bytes.length}, got ${sha256(released)}/${released.length}`,
    );
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === "--cleanup-legacy") {
    cleanupLegacyVendor();
    return;
  }

  const { descriptor } = loadPinnedDescriptor();
  const consumerDocsSha256 = verifyDocumentation(descriptor);
  if (args.length === 1 && args[0] === "--pin-only") {
    console.log(
      `Verified skill-audit ${descriptor.version} pin and documentation ${consumerDocsSha256}`,
    );
    return;
  }
  if (args.length === 2 && args[0] === "--release-descriptor") {
    verifyReleaseDescriptorBytes(resolve(args[1] ?? ""));
    console.log("Verified byte-identical skill-audit release descriptor");
    return;
  }
  if (args.length === 1 && args[0] === "--install") {
    await installExecutable(descriptor);
    return;
  }
  if (args.length !== 0) {
    throw new Error(
      "Usage: verify-skill-audit-release.mjs [--cleanup-legacy|--install|--pin-only|--release-descriptor PATH]",
    );
  }
  verifyExecutable(descriptor);
  console.log(
    `Verified skill-audit ${descriptor.version} executable, pin, and documentation ${consumerDocsSha256}`,
  );
}

if (
  process.argv[1] &&
  fileURLToPath(import.meta.url) === resolve(process.argv[1])
) {
  await main();
}
