import { readFileSync } from "node:fs";

/** @param {string[]} values */
export function uniqueSorted(values) {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

/**
 * @param {string} tool
 * @param {string} heading
 * @param {string[]} findings
 * @returns {boolean}
 */
function reportFindings(tool, heading, findings) {
  if (findings.length === 0) return false;
  console.error(`\n${tool}: ${heading}`);
  for (const finding of findings) console.error(`- ${finding}`);
  return true;
}

/**
 * @param {{
 *   baselinePath: string,
 *   current: Record<string, string[]>,
 *   label: string,
 *   printBaseline: boolean,
 *   regenerationCommand: string,
 *   reviewNotes: Record<string, string>,
 * }} options
 */
export function checkQualityBaseline(options) {
  const {
    baselinePath,
    current,
    label,
    printBaseline,
    regenerationCommand,
    reviewNotes,
  } = options;
  if (printBaseline) {
    console.log(
      JSON.stringify(
        { version: 1, reviewNotes, acceptedFindings: current },
        null,
        2,
      ),
    );
    return;
  }

  const baseline =
    /** @type {{ version: number, acceptedFindings: Record<string, string[]> }} */ (
      // The caller supplies a fixed repository-root baseline path.
      // eslint-disable-next-line security/detect-non-literal-fs-filename
      JSON.parse(readFileSync(baselinePath, "utf8"))
    );
  if (baseline.version !== 1) {
    throw new Error(
      `Unsupported ${label} quality baseline version: ${baseline.version}`,
    );
  }

  let failed = false;
  for (const [tool, findings] of Object.entries(current)) {
    const accepted = new Set(baseline.acceptedFindings[tool] ?? []);
    const actual = new Set(findings);
    const newFindings = findings.filter((finding) => !accepted.has(finding));
    const staleFindings = [...accepted].filter(
      (finding) => !actual.has(finding),
    );

    console.log(`${tool}: ${findings.length} accepted finding(s)`);
    failed = reportFindings(tool, "new findings", newFindings) || failed;
    failed =
      reportFindings(tool, "stale baseline findings", staleFindings) || failed;
  }

  if (failed) {
    console.error(
      `\nReview the changes, then regenerate with: ${regenerationCommand}`,
    );
    process.exitCode = 1;
  }
}
