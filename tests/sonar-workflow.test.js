const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");

const workflow = readFileSync(
  join(__dirname, "..", ".github", "workflows", "sonar.yml"),
  "utf8",
);

/**
 * @param {string} name
 * @returns {string}
 */
function workflowStep(name) {
  const marker = `      - name: ${name}\n`;
  const start = workflow.indexOf(marker);
  assert.notEqual(start, -1, `missing workflow step: ${name}`);
  const next = workflow.indexOf("\n      - name:", start + marker.length);
  return workflow.slice(start, next === -1 ? workflow.length : next);
}

test("classifies pull-request trust before checkout without loading secrets", () => {
  const classifier = workflowStep("Classify pull request trust");

  assert.ok(
    workflow.indexOf("Classify pull request trust") <
      workflow.indexOf("Check out full history"),
  );
  assert.match(
    classifier,
    /github\.event\.pull_request\.user\.login == 'dependabot\[bot\]'/,
  );
  assert.match(
    classifier,
    /github\.event\.pull_request\.head\.repo\.full_name != github\.repository/,
  );
  assert.doesNotMatch(
    classifier,
    /github\.event\.pull_request\.head\.repo\.fork/,
  );
  assert.doesNotMatch(classifier, /github\.actor|secrets\.SONAR_TOKEN/);
});

test("gates every repository-controlled and secret-bearing step", () => {
  for (const name of [
    "Check out full history",
    "Check for SonarQube Cloud token",
    "Scan and wait for the quality gate",
  ]) {
    assert.match(
      workflowStep(name),
      /if: steps\.trust\.outputs\.trusted == 'true'/,
      `workflow step is not trust-gated: ${name}`,
    );
  }

  assert.match(
    workflowStep("Check for SonarQube Cloud token"),
    /if \[ -z "\$SONAR_TOKEN" \]/,
  );
  assert.match(
    workflowStep("Scan and wait for the quality gate"),
    /-Dsonar\.qualitygate\.wait=true/,
  );
});
