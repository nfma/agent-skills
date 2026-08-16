#!/usr/bin/env node

// Antigravity probes servers with a proprietary server/discover request that
// huggingface.co rejects at the HTTP layer. Answer it locally and forward all
// standard MCP traffic unchanged.
const { spawn } = require("child_process");
const path = require("node:path");
const readline = require("readline");

if (process.argv.length !== 2) {
  throw new Error("hf-mcp-filter.js does not accept arguments");
}

const child = spawn(
  path.join(__dirname, "npx"),
  [
    "-y",
    "mcp-remote@0.1.38",
    "https://huggingface.co/mcp",
    "--header",
    "Authorization: Bearer ${HF_MCP_TOKEN}",
  ],
  {
    stdio: ["pipe", "inherit", "inherit"],
  },
);
child.on("exit", (code) => process.exit(code ?? 1));

const input = readline.createInterface({
  input: process.stdin,
  terminal: false,
});
input.on("line", (line) => {
  try {
    const message = /** @type {{ method?: unknown, id?: unknown }} */ (
      JSON.parse(line)
    );
    if (message.method === "server/discover" && message.id !== undefined) {
      process.stdout.write(
        `${JSON.stringify({
          jsonrpc: "2.0",
          id: message.id,
          error: { code: -32601, message: "Method not found" },
        })}\n`,
      );
      return;
    }
  } catch {
    process.stdout.write(
      `${JSON.stringify({
        jsonrpc: "2.0",
        id: null,
        error: { code: -32700, message: "Parse error" },
      })}\n`,
    );
    return;
  }
  child.stdin.write(`${line}\n`);
});
input.on("close", () => child.stdin.end());
