#!/usr/bin/env node

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

const [requestedConfigPath, serverName, timeoutValue] = process.argv.slice(2);

function fail(message, exitCode = 64) {
  console.error(`update-codex-mcp-config: ${message}`);
  process.exit(exitCode);
}

if (
  !requestedConfigPath ||
  !path.isAbsolute(requestedConfigPath) ||
  requestedConfigPath === path.parse(requestedConfigPath).root
) {
  fail("config path must be an absolute file path");
}

if (!serverName || !/^[A-Za-z0-9_-]+$/.test(serverName)) {
  fail("server name contains unsupported characters");
}

if (
  !timeoutValue ||
  !Number.isFinite(Number(timeoutValue)) ||
  Number(timeoutValue) <= 0
) {
  fail("startup timeout must be a positive number");
}

let configPath;
let source;
let sourceMode;
try {
  configPath = fs.realpathSync(requestedConfigPath);
  const openFlags = fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0);
  const configDescriptor = fs.openSync(configPath, openFlags);
  let isRegularFile = false;
  try {
    const metadata = fs.fstatSync(configDescriptor);
    isRegularFile = metadata.isFile();
    if (isRegularFile) {
      source = fs.readFileSync(configDescriptor, "utf8");
      sourceMode = metadata.mode & 0o777;
    }
  } finally {
    fs.closeSync(configDescriptor);
  }

  if (!isRegularFile) {
    fail(`config path is not a regular file: ${requestedConfigPath}`, 66);
  }
} catch (error) {
  fail(`cannot read ${requestedConfigPath}: ${error.message}`, 66);
}

function advanceQuotedValue(state, character) {
  if (!state.quote) {
    return false;
  }

  if (state.quote === '"' && state.escaped) {
    state.escaped = false;
  } else if (state.quote === '"' && character === "\\") {
    state.escaped = true;
  } else if (character === state.quote) {
    state.quote = "";
  }
  return true;
}

function updateContainerDepth(state, character) {
  switch (character) {
    case "[":
      state.squareDepth += 1;
      break;
    case "]":
      state.squareDepth -= 1;
      break;
    case "{":
      state.curlyDepth += 1;
      break;
    case "}":
      state.curlyDepth -= 1;
      break;
  }
}

function hasBalancedSingleLineValue(value) {
  const candidate = value.trim();
  if (
    !candidate ||
    candidate.startsWith("#") ||
    candidate.includes('"""') ||
    candidate.includes("'''")
  ) {
    return false;
  }

  const state = {
    quote: "",
    escaped: false,
    squareDepth: 0,
    curlyDepth: 0,
  };

  for (const character of candidate) {
    if (advanceQuotedValue(state, character)) {
      continue;
    }
    if (character === '"' || character === "'") {
      state.quote = character;
      continue;
    }
    if (character === "#") {
      break;
    }

    updateContainerDepth(state, character);
    if (state.squareDepth < 0 || state.curlyDepth < 0) {
      return false;
    }
  }

  return (
    state.quote === "" && state.squareDepth === 0 && state.curlyDepth === 0
  );
}

function isCanonicalTableHeader(line) {
  const candidate = line.trim();
  const isArrayTable = candidate.startsWith("[[");
  const closingDelimiter = isArrayTable ? "]]" : "]";
  const openingLength = isArrayTable ? 2 : 1;

  if (!candidate.startsWith("[")) {
    return false;
  }

  const closingIndex = candidate.indexOf(closingDelimiter, openingLength);
  if (closingIndex < openingLength) {
    return false;
  }

  const tableName = candidate.slice(openingLength, closingIndex);
  const suffix = candidate.slice(closingIndex + closingDelimiter.length).trim();
  return (
    tableName.length > 0 &&
    !tableName.includes("[") &&
    !tableName.includes("]") &&
    (suffix === "" || suffix.startsWith("#"))
  );
}

const sectionHeader = `[mcp_servers.${serverName}]`;
const lines = source.split("\n");
const sectionIndexes = [];

for (let index = 0; index < lines.length; index += 1) {
  if (lines[index].trim() === sectionHeader) {
    sectionIndexes.push(index);
  }
}

if (sectionIndexes.length !== 1) {
  fail(`expected exactly one ${sectionHeader} table`);
}

const sectionStart = sectionIndexes[0];
const assignmentPattern = /^\s*([A-Za-z0-9_-]+)\s*=(.*)$/;
let sectionEnd = lines.length;
const timeoutIndexes = [];
for (let index = sectionStart + 1; index < lines.length; index += 1) {
  const line = lines[index];
  if (isCanonicalTableHeader(line)) {
    sectionEnd = index;
    break;
  }

  const trimmedLine = line.trim();
  if (!trimmedLine || trimmedLine.startsWith("#")) {
    continue;
  }

  const assignment = assignmentPattern.exec(line);
  if (!assignment || !hasBalancedSingleLineValue(assignment[2])) {
    fail(`${sectionHeader} contains unsupported non-canonical TOML`);
  }
  if (assignment[1] === "startup_timeout_sec") {
    timeoutIndexes.push(index);
  }
}

if (timeoutIndexes.length > 1) {
  fail(`${sectionHeader} contains duplicate startup_timeout_sec keys`);
}

const timeoutLine = `startup_timeout_sec = ${Number(timeoutValue)}`;
if (timeoutIndexes.length === 1) {
  lines[timeoutIndexes[0]] = timeoutLine;
} else {
  let insertAt = sectionEnd;
  while (insertAt > sectionStart + 1 && lines[insertAt - 1].trim() === "") {
    insertAt -= 1;
  }
  lines.splice(insertAt, 0, timeoutLine);
}

const temporaryPath = `${configPath}.${process.pid}.${randomUUID()}.tmp`;
try {
  fs.writeFileSync(temporaryPath, lines.join("\n"), {
    encoding: "utf8",
    flag: "wx",
    mode: sourceMode,
  });
  fs.chmodSync(temporaryPath, sourceMode);
  fs.renameSync(temporaryPath, configPath);
} catch (error) {
  try {
    fs.unlinkSync(temporaryPath);
  } catch (cleanupError) {
    if (cleanupError.code !== "ENOENT") {
      console.error(
        `update-codex-mcp-config: cannot remove ${temporaryPath}: ${cleanupError.message}`,
      );
    }
  }
  fail(`cannot update ${configPath}: ${error.message}`, 74);
}
