#!/usr/bin/env node

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { spawnSync } = require("node:child_process");

const updater = path.resolve(
  __dirname,
  "../scripts/update-codex-mcp-config.cjs",
);

/**
 * @param {string} source
 * @param {(configPath: string) => void} callback
 */
function withConfig(source, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "codex-mcp-config-"));
  const configPath = path.join(directory, "config.toml");
  fs.writeFileSync(configPath, source, { mode: 0o600 });
  try {
    callback(configPath);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

/**
 * @param {string} configPath
 * @param {string} [timeout]
 */
function runUpdater(configPath, timeout = "20") {
  return spawnSync(
    process.execPath,
    [updater, configPath, "chrome-devtools", timeout],
    { encoding: "utf8" },
  );
}

/**
 * @param {string} source
 * @param {number} expectedStatus
 * @param {RegExp} expectedMessage
 */
function assertFailsClosed(source, expectedStatus, expectedMessage) {
  withConfig(source, (configPath) => {
    const original = fs.readFileSync(configPath, "utf8");
    const result = runUpdater(configPath);

    assert.equal(result.status, expectedStatus);
    assert.match(result.stderr, expectedMessage);
    assert.equal(fs.readFileSync(configPath, "utf8"), original);
  });
}

test("adds the startup timeout without discarding other server settings", () => {
  withConfig(
    `model = "gpt-5.6"\n\n[mcp_servers.chrome-devtools]\ncommand = "/Users/test/.local/bin/chrome-devtools-vivaldi"\nenabled_tools = ["list_pages"]\n\n[mcp_servers.serena]\ncommand = "/Users/test/.local/bin/serena-mcp"\n`,
    (configPath) => {
      const result = runUpdater(configPath);
      assert.equal(result.status, 0, result.stderr);

      const updated = fs.readFileSync(configPath, "utf8");
      assert.match(
        updated,
        /enabled_tools = \["list_pages"\]\nstartup_timeout_sec = 20/,
      );
      assert.match(updated, /\[mcp_servers\.serena\]\ncommand =/);
      assert.equal(fs.statSync(configPath).mode & 0o777, 0o600);
    },
  );
});

test("updates the timeout idempotently", () => {
  withConfig(
    `[mcp_servers.chrome-devtools]\ncommand = "npx"\nstartup_timeout_sec = 10.0\n`,
    (configPath) => {
      const first = runUpdater(configPath);
      const second = runUpdater(configPath);
      assert.equal(first.status, 0, first.stderr);
      assert.equal(second.status, 0, second.stderr);

      const updated = fs.readFileSync(configPath, "utf8");
      assert.equal((updated.match(/startup_timeout_sec/g) || []).length, 1);
      assert.match(updated, /startup_timeout_sec = 20/);
    },
  );
});

test("rejects a multiline value before writing", () => {
  assertFailsClosed(
    `[mcp_servers.chrome-devtools]\ncommand = "npx"\nenabled_tools =\n[\n  "list_pages",\n]\n`,
    64,
    /unsupported non-canonical TOML/,
  );
});

test("rejects TOML multiline strings before writing", () => {
  const multilineStrings = [
    `[mcp_servers.chrome-devtools]\nprompt = """Say "hello\nstartup_timeout_sec = "10"\nend = "done"""\n`,
    `[mcp_servers.chrome-devtools]\nprompt = '''Say 'hello\n[looks like a header]\nstill inside'''\n`,
  ];

  for (const source of multilineStrings) {
    assertFailsClosed(source, 64, /unsupported non-canonical TOML/);
  }
});

test("rejects a quoted timeout key before writing", () => {
  assertFailsClosed(
    `[mcp_servers.chrome-devtools]\ncommand = "npx"\n"startup_timeout_sec" = 10\n`,
    64,
    /unsupported non-canonical TOML/,
  );
});

test("rejects duplicate server tables before writing", () => {
  assertFailsClosed(
    `[mcp_servers.chrome-devtools]\ncommand = "npx"\n\n[mcp_servers.chrome-devtools]\ncommand = "other"\n`,
    64,
    /expected exactly one/,
  );
});

test("reports an unavailable config without creating it", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "codex-mcp-config-"));
  const configPath = path.join(directory, "missing.toml");
  try {
    const result = runUpdater(configPath);
    assert.equal(result.status, 66);
    assert.match(result.stderr, /cannot read/);
    assert.equal(fs.existsSync(configPath), false);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("updates a symlink target without replacing the symlink", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "codex-mcp-config-"));
  const targetPath = path.join(directory, "managed-config.toml");
  const configPath = path.join(directory, "config.toml");
  fs.writeFileSync(
    targetPath,
    `[mcp_servers.chrome-devtools]\ncommand = "npx"\n`,
    { mode: 0o600 },
  );
  fs.symlinkSync("managed-config.toml", configPath);

  try {
    const result = runUpdater(configPath);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(fs.lstatSync(configPath).isSymbolicLink(), true);
    assert.match(
      fs.readFileSync(targetPath, "utf8"),
      /startup_timeout_sec = 20/,
    );
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test(
  "returns 74 when atomic replacement cannot be created",
  {
    skip: process.platform === "win32" || process.getuid?.() === 0,
  },
  () => {
    const directory = fs.mkdtempSync(
      path.join(os.tmpdir(), "codex-mcp-config-"),
    );
    const configPath = path.join(directory, "config.toml");
    const source = `[mcp_servers.chrome-devtools]\ncommand = "npx"\n`;
    fs.writeFileSync(configPath, source, { mode: 0o600 });
    fs.chmodSync(directory, 0o500);

    let result;
    try {
      result = runUpdater(configPath);
    } finally {
      fs.chmodSync(directory, 0o700);
    }

    try {
      assert.equal(result.status, 74);
      assert.match(result.stderr, /cannot update/);
      assert.equal(fs.readFileSync(configPath, "utf8"), source);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  },
);
