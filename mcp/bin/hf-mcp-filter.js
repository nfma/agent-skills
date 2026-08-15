#!/usr/bin/env node

// Antigravity probes servers with a proprietary server/discover request that
// huggingface.co rejects at the HTTP layer. Answer it locally and forward all
// standard MCP traffic unchanged.
const { spawn } = require('child_process');
const readline = require('readline');

const child = spawn(process.argv[2], process.argv.slice(3), {
  stdio: ['pipe', 'inherit', 'inherit'],
});
child.on('exit', (code) => process.exit(code ?? 1));

const input = readline.createInterface({ input: process.stdin, terminal: false });
input.on('line', (line) => {
  try {
    const message = JSON.parse(line);
    if (message.method === 'server/discover' && message.id !== undefined) {
      process.stdout.write(`${JSON.stringify({
        jsonrpc: '2.0',
        id: message.id,
        error: { code: -32601, message: 'Method not found' },
      })}\n`);
      return;
    }
  } catch (_) {
    // Non-JSON lines are valid transport input and must pass through.
  }
  child.stdin.write(`${line}\n`);
});
input.on('close', () => child.stdin.end());
