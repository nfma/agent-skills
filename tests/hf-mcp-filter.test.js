#!/usr/bin/env node

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { spawn } = require("node:child_process");

const filter = path.resolve(__dirname, "../mcp/bin/hf-mcp-filter.js");

/**
 * @typedef {{
 *   id?: number | null,
 *   error?: { code?: number, message?: string },
 *   [key: string]: unknown
 * }} JsonRpcMessage
 */

/**
 * @returns {{ argsFile: string, directory: string, filter: string }}
 */
function createFilterHarness() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "hf-mcp-filter-"));
  const temporaryFilter = path.join(directory, "hf-mcp-filter.js");
  const fakeNpx = path.join(directory, "npx");
  const argsFile = path.join(directory, "args.txt");
  fs.copyFileSync(filter, temporaryFilter);
  fs.writeFileSync(
    fakeNpx,
    '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$HF_MCP_FILTER_ARGS_FILE"\nexec /bin/cat\n',
    { mode: 0o755 },
  );
  return { argsFile, directory, filter: temporaryFilter };
}

/**
 * @param {{ argsFile: string, filter: string }} harness
 * @param {string[]} lines
 * @returns {Promise<JsonRpcMessage[]>}
 */
function runFilter(harness, lines) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [harness.filter], {
      env: {
        ...process.env,
        HF_MCP_FILTER_ARGS_FILE: harness.argsFile,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`filter exited ${code}: ${stderr}`));
        return;
      }
      resolve(
        stdout
          .trim()
          .split("\n")
          .filter(Boolean)
          .map((line) => /** @type {JsonRpcMessage} */ (JSON.parse(line))),
      );
    });

    child.stdin.end(`${lines.join("\n")}\n`);
  });
}

test("forwards valid messages and handles local protocol errors", async (context) => {
  const harness = createFilterHarness();
  context.after(() =>
    fs.rmSync(harness.directory, { force: true, recursive: true }),
  );
  const forwarded = { jsonrpc: "2.0", id: 1, method: "tools/list" };
  const messages = await runFilter(harness, [
    JSON.stringify(forwarded),
    JSON.stringify({ jsonrpc: "2.0", id: 2, method: "server/discover" }),
    "{malformed",
  ]);

  assert.equal(messages.length, 3);
  assert.deepEqual(
    messages.find((message) => message.id === 1),
    forwarded,
  );
  assert.ok(
    messages.some(
      (message) => message.id === 2 && message.error?.code === -32601,
    ),
  );
  assert.ok(
    messages.some(
      (message) =>
        message.id === null &&
        message.error?.code === -32700 &&
        message.error.message === "Parse error",
    ),
  );
  assert.deepEqual(
    fs.readFileSync(harness.argsFile, "utf8").trim().split("\n"),
    [
      "-y",
      "mcp-remote@0.1.38",
      "https://huggingface.co/mcp",
      "--header",
      "Authorization: Bearer ${HF_MCP_TOKEN}",
    ],
  );
});
