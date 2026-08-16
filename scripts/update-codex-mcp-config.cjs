#!/usr/bin/env node

'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const [requestedConfigPath, serverName, timeoutValue] = process.argv.slice(2);

function fail(message, exitCode = 64) {
  console.error(`update-codex-mcp-config: ${message}`);
  process.exit(exitCode);
}

if (!requestedConfigPath || !path.isAbsolute(requestedConfigPath) ||
    requestedConfigPath === path.parse(requestedConfigPath).root) {
  fail('config path must be an absolute file path');
}

if (!serverName || !/^[A-Za-z0-9_-]+$/.test(serverName)) {
  fail('server name contains unsupported characters');
}

if (!timeoutValue || !Number.isFinite(Number(timeoutValue)) ||
    Number(timeoutValue) <= 0) {
  fail('startup timeout must be a positive number');
}

let configPath;
let source;
let sourceMode;
try {
  configPath = fs.realpathSync(requestedConfigPath);
  if (!fs.statSync(configPath).isFile()) {
    fail(`config path is not a regular file: ${requestedConfigPath}`, 66);
  }
  source = fs.readFileSync(configPath, 'utf8');
  sourceMode = fs.statSync(configPath).mode & 0o777;
} catch (error) {
  fail(`cannot read ${requestedConfigPath}: ${error.message}`, 66);
}

function hasBalancedSingleLineValue(value) {
  let quote = '';
  let escaped = false;
  let squareDepth = 0;
  let curlyDepth = 0;

  for (const character of value.trim()) {
    if (quote) {
      if (quote === '"' && escaped) {
        escaped = false;
      } else if (quote === '"' && character === '\\') {
        escaped = true;
      } else if (character === quote) {
        quote = '';
      }
      continue;
    }

    if (character === '"' || character === "'") {
      quote = character;
    } else if (character === '#') {
      break;
    } else if (character === '[') {
      squareDepth += 1;
    } else if (character === ']') {
      squareDepth -= 1;
    } else if (character === '{') {
      curlyDepth += 1;
    } else if (character === '}') {
      curlyDepth -= 1;
    }

    if (squareDepth < 0 || curlyDepth < 0) {
      return false;
    }
  }

  return quote === '' && squareDepth === 0 && curlyDepth === 0 &&
    !/^\s*(?:#|$)/.test(value);
}

const sectionHeader = `[mcp_servers.${serverName}]`;
const lines = source.split('\n');
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
const tableHeaderPattern =
  /^\s*(?:\[[^\[\]\r\n]+\]|\[\[[^\[\]\r\n]+\]\])\s*(?:#.*)?$/;
const assignmentPattern = /^\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$/;
let sectionEnd = lines.length;
const timeoutIndexes = [];
for (let index = sectionStart + 1; index < lines.length; index += 1) {
  const line = lines[index];
  if (tableHeaderPattern.test(line)) {
    sectionEnd = index;
    break;
  }

  if (/^\s*(?:#.*)?$/.test(line)) {
    continue;
  }

  const assignment = assignmentPattern.exec(line);
  if (!assignment || !hasBalancedSingleLineValue(assignment[2])) {
    fail(`${sectionHeader} contains unsupported non-canonical TOML`);
  }
  if (assignment[1] === 'startup_timeout_sec') {
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
  while (insertAt > sectionStart + 1 && lines[insertAt - 1].trim() === '') {
    insertAt -= 1;
  }
  lines.splice(insertAt, 0, timeoutLine);
}

const temporaryPath = `${configPath}.${process.pid}.${randomUUID()}.tmp`;
try {
  fs.writeFileSync(temporaryPath, lines.join('\n'), {
    encoding: 'utf8',
    flag: 'wx',
    mode: sourceMode,
  });
  fs.chmodSync(temporaryPath, sourceMode);
  fs.renameSync(temporaryPath, configPath);
} catch (error) {
  try {
    fs.unlinkSync(temporaryPath);
  } catch (cleanupError) {
    if (cleanupError.code !== 'ENOENT') {
      console.error(
        `update-codex-mcp-config: cannot remove ${temporaryPath}: ${cleanupError.message}`,
      );
    }
  }
  fail(`cannot update ${configPath}: ${error.message}`, 74);
}
