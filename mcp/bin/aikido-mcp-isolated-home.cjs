'use strict';

const os = require('node:os');

const isolatedHome = process.env.AIKIDO_MCP_ISOLATED_HOME;
if (!isolatedHome) {
  throw new Error('AIKIDO_MCP_ISOLATED_HOME is required');
}

os.homedir = () => isolatedHome;
