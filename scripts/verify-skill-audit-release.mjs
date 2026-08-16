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

/** @param {unknown} value @returns {asserts value is ReleaseDescriptor} */
function assertDescriptor(value) {
  if (!value || typeof value !== "object") {
    throw new Error("skill-audit release descriptor must be an object");
  }
  const descriptor = /** @type {Record<string, any>} */ (value);
  if (
    descriptor.schemaVersion !== 1 ||
    descriptor.sourceRepository !== "nfma/skill-audit" ||
    descriptor.tag !== `v${descriptor.version}` ||
    descriptor.buildWorkflow !==
      `.github/workflows/release.yml@${descriptor.sourceCommit}` ||
    !/^[0-9a-f]{40}$/.test(descriptor.sourceCommit ?? "") ||
    !isVersionAtLeast(process.versions.node, descriptor.minimumNode)
  ) {
    throw new Error("skill-audit release descriptor identity is invalid");
  }
  const executable = descriptor.executable;
  if (
    !executable ||
    executable.name !== `skill-audit-v${descriptor.version}.mjs` ||
    !/^[0-9a-f]{64}$/.test(executable.sha256 ?? "") ||
    !/^[0-9a-f]{64}$/.test(executable.embeddedRulesSha256 ?? "") ||
    !Number.isSafeInteger(executable.sizeBytes) ||
    executable.sizeBytes <= 0 ||
    !Array.isArray(executable.exports) ||
    executable.exports.length !== 6
  ) {
    throw new Error("skill-audit executable identity is invalid");
  }
  const documentation = descriptor.documentation;
  if (
    !documentation ||
    !Array.isArray(documentation.files) ||
    documentation.files.length !== 6 ||
    !/^[0-9a-f]{64}$/.test(documentation.upstreamDocsSha256 ?? "")
  ) {
    throw new Error("skill-audit documentation identity is invalid");
  }
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
      "skill-audit executable does not bind the pinned rules digest",
    );
  }
  const encodedMatch = source
    .slice(markerIndex)
    // The input is the size- and digest-pinned release executable.
    // eslint-disable-next-line security/detect-unsafe-regex
    .match(/,[$A-Za-z_][$\w]*=(\[(?:"[A-Za-z0-9+/=]*",?)+\])\.join\(""\)/);
  if (!encodedMatch) {
    throw new Error("skill-audit embedded rules payload could not be located");
  }
  const encodedChunks = encodedMatch[1];
  if (!encodedChunks) {
    throw new Error("skill-audit embedded rules payload is empty");
  }
  const chunks = /** @type {string[]} */ (JSON.parse(encodedChunks));
  const encoded = chunks.join("");
  const decoded = Buffer.from(encoded, "base64");
  if (decoded.toString("base64") !== encoded) {
    throw new Error("skill-audit embedded rules are not canonical base64");
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
  if ((syntax.stdout ?? "") !== "" || (syntax.stderr ?? "") !== "") {
    throw new Error("skill-audit syntax check emitted unexpected output");
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
    if (readdirSync(probeRoot).length !== 0) {
      throw new Error("skill-audit import left files in the probe directory");
    }
    const directoryAfter = readdirSync(executableDirectory)
      .filter((entry) => entry !== probeName)
      .sort();
    if (JSON.stringify(directoryBefore) !== JSON.stringify(directoryAfter)) {
      throw new Error("skill-audit import changed the executable directory");
    }
    if (sha256(readFileSync(path)) !== descriptor.executable.sha256) {
      throw new Error("skill-audit import changed the executable");
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
    throw new Error(
      "Refusing to remove legacy skill-audit with untracked files",
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
      "Refusing to remove legacy skill-audit with staged changes",
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

/** @param {ReleaseDescriptor} descriptor */
async function installExecutable(descriptor) {
  try {
    verifyExecutable(descriptor, executablePath);
    console.log(`Verified installed skill-audit ${descriptor.version}`);
    return;
  } catch (error) {
    if (
      !(error instanceof Error) ||
      !error.message.includes("identity mismatch")
    ) {
      try {
        lstatSync(executablePath);
      } catch (statError) {
        if (
          /** @type {NodeJS.ErrnoException} */ (statError).code !== "ENOENT"
        ) {
          throw statError;
        }
      }
    }
  }

  const destinationDirectory = dirname(executablePath);
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
    const response = await fetch(releaseDownloadUrl(descriptor), {
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
    chmodSync(temporaryPath, 0o755);
    renameSync(temporaryPath, executablePath);
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
      "Tracked skill-audit pin differs from the release descriptor asset",
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
