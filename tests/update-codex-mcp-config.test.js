#!/usr/bin/env node

'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { spawnSync } = require('node:child_process');

const updater = path.resolve(__dirname, '../scripts/update-codex-mcp-config.cjs');

function withConfig(source, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-mcp-config-'));
  const configPath = path.join(directory, 'config.toml');
  fs.writeFileSync(configPath, source, { mode: 0o600 });
  try {
    callback(configPath);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

function runUpdater(configPath, timeout = '20') {
  return spawnSync(process.execPath, [
    updater,
    configPath,
    'chrome-devtools',
    timeout,
  ], { encoding: 'utf8' });
}

test('adds the startup timeout without discarding other server settings', () => {
  withConfig(`model = "gpt-5.6"\n\n[mcp_servers.chrome-devtools]\ncommand = "/Users/test/.local/bin/chrome-devtools-vivaldi"\nenabled_tools = ["list_pages"]\n\n[mcp_servers.serena]\ncommand = "/Users/test/.local/bin/serena-mcp"\n`, (configPath) => {
    const result = runUpdater(configPath);
    assert.equal(result.status, 0, result.stderr);

    const updated = fs.readFileSync(configPath, 'utf8');
    assert.match(updated, /enabled_tools = \["list_pages"\]\nstartup_timeout_sec = 20/);
    assert.match(updated, /\[mcp_servers\.serena\]\ncommand =/);
    assert.equal(fs.statSync(configPath).mode & 0o777, 0o600);
  });
});

test('updates the timeout idempotently', () => {
  withConfig(`[mcp_servers.chrome-devtools]\ncommand = "npx"\nstartup_timeout_sec = 10.0\n`, (configPath) => {
    const first = runUpdater(configPath);
    const second = runUpdater(configPath);
    assert.equal(first.status, 0, first.stderr);
    assert.equal(second.status, 0, second.stderr);

    const updated = fs.readFileSync(configPath, 'utf8');
    assert.equal((updated.match(/startup_timeout_sec/g) || []).length, 1);
    assert.match(updated, /startup_timeout_sec = 20/);
  });
});
