#!/usr/bin/env node

"use strict";

/* eslint-disable security/detect-non-literal-fs-filename -- Every dynamic path is rooted in a fresh test-only temporary directory. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { spawnSync } = require("node:child_process");
const fc = require("fast-check");

const updater = path.resolve(
  __dirname,
  "../../scripts/update-codex-mcp-config.cjs",
);

/**
 * @param {string} configPath
 * @param {number} timeout
 */
function runUpdater(configPath, timeout) {
  return spawnSync(
    process.execPath,
    [updater, configPath, "chrome-devtools", String(timeout)],
    { encoding: "utf8" },
  );
}

/** @param {string} source */
function timeoutAssignments(source) {
  return source
    .split("\n")
    .filter((line) => /^\s*startup_timeout_sec\s*=/.test(line));
}

test(
  "fuzzed target tables either update atomically or fail without mutation",
  {
    timeout: 60_000,
  },
  () => {
    fc.assert(
      fc.property(
        fc.string({ maxLength: 512 }),
        fc.integer({ min: 1, max: 3_600 }),
        (tableBody, timeout) => {
          const directory = fs.mkdtempSync(
            path.join(os.tmpdir(), "codex-mcp-fuzz-"),
          );
          const configPath = path.join(directory, "config.toml");
          const source = `[mcp_servers.chrome-devtools]\n${tableBody}\n`;
          fs.writeFileSync(configPath, source, { mode: 0o600 });

          try {
            const result = runUpdater(configPath, timeout);
            const updated = fs.readFileSync(configPath, "utf8");
            const temporaryFiles = fs
              .readdirSync(directory)
              .filter((entry) => entry.endsWith(".tmp"));
            assert.deepEqual(temporaryFiles, []);

            if (result.status !== 0) {
              assert.equal(result.status, 64);
              assert.equal(updated, source);
              return;
            }

            assert.deepEqual(timeoutAssignments(updated), [
              `startup_timeout_sec = ${timeout}`,
            ]);
            const second = runUpdater(configPath, timeout);
            assert.equal(second.status, 0, second.stderr);
            assert.equal(fs.readFileSync(configPath, "utf8"), updated);
          } finally {
            fs.rmSync(directory, { recursive: true, force: true });
          }
        },
      ),
      { numRuns: 100 },
    );
  },
);
