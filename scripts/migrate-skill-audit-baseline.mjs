#!/usr/bin/env node

import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  ACCEPTED_FINDING_BASELINE_VERSION,
  migrateAcceptedFindingBaseline,
  validateAcceptedFindingBaseline,
} from "./skill-audit-accepted-findings.mjs";

/** @param {string[]} argv */
function parseArguments(argv) {
  if (argv.length === 0) {
    return resolve(
      fileURLToPath(new URL("..", import.meta.url)),
      ".skill-audit-baseline.json",
    );
  }
  if (argv.length === 2 && argv[0] === "--baseline") {
    const baselineArgument = argv[1];
    if (baselineArgument) return resolve(baselineArgument);
  }
  throw new Error("Usage: migrate-skill-audit-baseline [--baseline <path>]");
}

/** @param {string[]} argv */
function main(argv) {
  const baselinePath = parseArguments(argv);
  // eslint-disable-next-line security/detect-non-literal-fs-filename -- The operator explicitly selects the baseline migration target.
  const baseline = JSON.parse(readFileSync(baselinePath, "utf8"));
  if (baseline.version === ACCEPTED_FINDING_BASELINE_VERSION) {
    validateAcceptedFindingBaseline(baseline);
    console.log(
      `Skill-audit baseline is already version ${ACCEPTED_FINDING_BASELINE_VERSION}: ${baselinePath}`,
    );
    return;
  }

  const migrated = migrateAcceptedFindingBaseline(baseline);
  // eslint-disable-next-line security/detect-non-literal-fs-filename -- The validated migration is written back to the operator-selected baseline.
  writeFileSync(baselinePath, `${JSON.stringify(migrated, null, 2)}\n`);
  console.log(
    `Migrated ${migrated.acceptedFindings.length} accepted findings to content-addressed baseline version ${ACCEPTED_FINDING_BASELINE_VERSION}: ${baselinePath}`,
  );
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
