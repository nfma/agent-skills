#!/usr/bin/env node

'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const [configPath, serverName, timeoutValue] = process.argv.slice(2);

function fail(message, exitCode = 64) {
  console.error(`update-codex-mcp-config: ${message}`);
  process.exit(exitCode);
}

if (!configPath || !path.isAbsolute(configPath) ||
    configPath === path.parse(configPath).root) {
  fail('config path must be an absolute file path');
}

if (!serverName || !/^[A-Za-z0-9_-]+$/.test(serverName)) {
  fail('server name contains unsupported characters');
}

if (!timeoutValue || !Number.isFinite(Number(timeoutValue)) ||
    Number(timeoutValue) <= 0) {
  fail('startup timeout must be a positive number');
}

let source;
let sourceMode;
try {
  source = fs.readFileSync(configPath, 'utf8');
  sourceMode = fs.statSync(configPath).mode & 0o777;
} catch (error) {
  fail(`cannot read ${configPath}: ${error.message}`, 66);
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
let sectionEnd = lines.length;
for (let index = sectionStart + 1; index < lines.length; index += 1) {
  if (/^\s*\[/.test(lines[index])) {
    sectionEnd = index;
    break;
  }
}

const timeoutIndexes = [];
for (let index = sectionStart + 1; index < sectionEnd; index += 1) {
  if (/^\s*startup_timeout_sec\s*=/.test(lines[index])) {
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
  while (insertAt > sectionStart + 1 && lines[insertAt - 1] === '') {
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
