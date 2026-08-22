const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");

const workflow = readFileSync(
  join(__dirname, "..", ".github", "workflows", "sonar.yml"),
  "utf8",
);
const dependabotWorkflow = readFileSync(
  join(__dirname, "..", ".github", "workflows", "dependabot-auto-merge.yml"),
  "utf8",
);

/**
 * @param {string} contents
 * @param {string} name
 * @returns {string}
 */
function workflowStepFrom(contents, name) {
  const marker = `      - name: ${name}\n`;
  const start = contents.indexOf(marker);
  assert.notEqual(start, -1, `missing workflow step: ${name}`);
  const next = contents.indexOf("\n      - name:", start + marker.length);
  return contents.slice(start, next === -1 ? contents.length : next);
}

/**
 * @param {string} name
 * @returns {string}
 */
function workflowStep(name) {
  return workflowStepFrom(workflow, name);
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

test("scopes Dependabot auto-merge to same-repository bot pull requests", () => {
  assert.match(dependabotWorkflow, /pull_request:/);
  assert.match(
    dependabotWorkflow,
    /github\.event\.pull_request\.user\.login == 'dependabot\[bot\]'/,
  );
  assert.match(
    dependabotWorkflow,
    /github\.event\.pull_request\.head\.repo\.full_name == github\.repository/,
  );
  assert.match(
    dependabotWorkflow,
    /github\.event\.pull_request\.draft == false/,
  );
  assert.doesNotMatch(dependabotWorkflow, /pull_request_target:/);
  assert.doesNotMatch(dependabotWorkflow, /actions\/checkout|secrets\./);
});

test("grants only merge permissions and forces squash auto-merge", () => {
  assert.match(dependabotWorkflow, /permissions: \{\}/);
  const writePermissions = dependabotWorkflow
    .split("\n")
    .map((line) => line.split(" #", 1)[0] ?? "")
    .filter((line) => line.endsWith(": write"));
  assert.deepEqual(writePermissions, [
    "      contents: write",
    "      pull-requests: write",
  ]);
  assert.match(
    dependabotWorkflow,
    /gh pr merge --repo "\$GH_REPO" --auto --squash "\$PR_NUMBER"/,
  );
  assert.doesNotMatch(dependabotWorkflow, /--merge\b|--rebase\b/);
});

test("auto-merges only patch and minor source-dependency updates", () => {
  const metadata = workflowStepFrom(
    dependabotWorkflow,
    "Inspect Dependabot update",
  );
  const merge = workflowStepFrom(
    dependabotWorkflow,
    "Enable squash auto-merge",
  );
  const manual = workflowStepFrom(
    dependabotWorkflow,
    "Explain manual review requirement",
  );

  assert.match(
    metadata,
    /dependabot\/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98 # v3\.1\.0/,
  );
  assert.match(merge, /package-ecosystem == 'npm_and_yarn'/);
  assert.doesNotMatch(merge, /package-ecosystem == 'npm'/);
  assert.match(merge, /package-ecosystem == 'uv'/);
  assert.doesNotMatch(merge, /package-ecosystem == 'gitsubmodule'/);
  assert.doesNotMatch(merge, /package-ecosystem == 'submodules'/);
  assert.doesNotMatch(merge, /package-ecosystem == 'github_actions'/);
  assert.match(merge, /update-type == 'version-update:semver-patch'/);
  assert.match(merge, /update-type == 'version-update:semver-minor'/);
  assert.doesNotMatch(merge, /version-update:semver-major/);
  assert.match(manual, /package-ecosystem != 'npm_and_yarn'/);
  assert.doesNotMatch(manual, /package-ecosystem != 'npm'/);
  assert.match(manual, /package-ecosystem != 'uv'/);
  assert.doesNotMatch(manual, /package-ecosystem != 'gitsubmodule'/);
  assert.doesNotMatch(manual, /package-ecosystem != 'submodules'/);
  assert.match(manual, /update-type != 'version-update:semver-patch'/);
  assert.match(manual, /update-type != 'version-update:semver-minor'/);
  assert.doesNotMatch(manual, /gh pr merge/);
});
