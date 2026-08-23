const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const { parse, stringify } = require("yaml");

const workflow = readFileSync(
  join(__dirname, "..", ".github", "workflows", "sonar.yml"),
  "utf8",
);
const dependabotWorkflow = readFileSync(
  join(__dirname, "..", ".github", "workflows", "dependabot-auto-merge.yml"),
  "utf8",
);
const dependabotConfig = readFileSync(
  join(__dirname, "..", ".github", "dependabot.yml"),
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

/**
 * @param {string} contents
 * @returns {Record<string, unknown>[]}
 */
function parseDependabotUpdates(contents) {
  const config = parse(contents);
  assert.ok(
    config && typeof config === "object" && !Array.isArray(config),
    "Dependabot config must be a mapping",
  );
  assert.ok(Array.isArray(config.updates), "Dependabot updates must be a list");
  for (const update of config.updates) {
    assert.ok(
      update && typeof update === "object" && !Array.isArray(update),
      "each Dependabot update must be a mapping",
    );
    assert.equal(
      typeof update["package-ecosystem"],
      "string",
      "each Dependabot update must name a package ecosystem",
    );
  }
  return config.updates;
}

/**
 * @param {string} contents
 */
function assertTypeScriptDependabotPolicy(contents) {
  const updates = parseDependabotUpdates(contents);
  const npmUpdates = updates.filter(
    (update) => update["package-ecosystem"] === "npm",
  );
  assert.ok(npmUpdates.length > 0, "missing Dependabot update block: npm");

  for (const [index, update] of npmUpdates.entries()) {
    assert.deepEqual(
      update.ignore,
      [{ "dependency-name": "typescript", versions: ["7.x"] }],
      `npm update block ${index + 1} must contain exactly the reviewed TypeScript ignore rule`,
    );
  }

  for (const ecosystem of ["uv", "github-actions"]) {
    const ecosystemUpdates = updates.filter(
      (update) => update["package-ecosystem"] === ecosystem,
    );
    assert.ok(
      ecosystemUpdates.length > 0,
      `missing Dependabot update block: ${ecosystem}`,
    );
    for (const update of ecosystemUpdates) {
      const ignore = update.ignore ?? [];
      assert.ok(
        Array.isArray(ignore),
        `${ecosystem} ignore policy must be a list`,
      );
      assert.equal(
        ignore.some(
          (rule) =>
            rule &&
            typeof rule === "object" &&
            rule["dependency-name"] === "typescript",
        ),
        false,
        `${ecosystem} updates must not ignore TypeScript`,
      );
    }
  }
}

/**
 * @param {(updates: Record<string, unknown>[]) => void} mutate
 * @returns {string}
 */
function mutateDependabotConfig(mutate) {
  const config = parse(dependabotConfig);
  mutate(config.updates);
  return stringify(config);
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

test("validates the TypeScript ignore policy across every npm block", () => {
  assert.doesNotThrow(() => assertTypeScriptDependabotPolicy(dependabotConfig));
});

test("rejects additional npm ignore entries", () => {
  for (const extraRule of [
    { "dependency-name": "*", versions: [">=0"] },
    { "dependency-name": "typescript" },
  ]) {
    const mutated = mutateDependabotConfig((updates) => {
      const npm = updates.find(
        (update) => update["package-ecosystem"] === "npm",
      );
      assert.ok(npm, "fixture must contain an npm update block");
      assert.ok(
        Array.isArray(npm.ignore),
        "fixture ignore policy must be a list",
      );
      npm.ignore.push(extraRule);
    });

    assert.throws(
      () => assertTypeScriptDependabotPolicy(mutated),
      /must contain exactly the reviewed TypeScript ignore rule/,
    );
  }
});

test("rejects a second npm block without the reviewed ignore policy", () => {
  const mutated = mutateDependabotConfig((updates) => {
    updates.push({
      "package-ecosystem": "npm",
      directory: "/packages/example",
      schedule: { interval: "weekly" },
    });
  });

  assert.throws(
    () => assertTypeScriptDependabotPolicy(mutated),
    /npm update block 2 must contain exactly the reviewed TypeScript ignore rule/,
  );
});

test("rejects missing required Dependabot ecosystems", () => {
  for (const ecosystem of ["npm", "uv", "github-actions"]) {
    const mutated = mutateDependabotConfig((updates) => {
      const retained = updates.filter(
        (update) => update["package-ecosystem"] !== ecosystem,
      );
      updates.splice(0, updates.length, ...retained);
    });

    assert.throws(
      () => assertTypeScriptDependabotPolicy(mutated),
      (error) => {
        assert.ok(error instanceof Error);
        assert.ok(
          error.message.includes(
            `missing Dependabot update block: ${ecosystem}`,
          ),
        );
        return true;
      },
    );
  }
});

test("rejects TypeScript ignores outside npm updates", () => {
  for (const ecosystem of ["uv", "github-actions"]) {
    const mutated = mutateDependabotConfig((updates) => {
      const update = updates.find(
        (candidate) => candidate["package-ecosystem"] === ecosystem,
      );
      assert.ok(update, `fixture must contain a ${ecosystem} update block`);
      update.ignore = [{ "dependency-name": "typescript", versions: ["7.x"] }];
    });

    assert.throws(
      () => assertTypeScriptDependabotPolicy(mutated),
      (error) => {
        assert.ok(error instanceof Error);
        assert.ok(
          error.message.includes(
            `${ecosystem} updates must not ignore TypeScript`,
          ),
        );
        return true;
      },
    );
  }
});

test("fails closed on malformed, duplicate-key, and merge-key YAML", () => {
  const duplicateKey = dependabotConfig.replace(
    '  - package-ecosystem: "npm"',
    '  - package-ecosystem: "npm"\n    package-ecosystem: "uv"',
  );
  const mergeKey = dependabotConfig.replace(
    '  - package-ecosystem: "npm"',
    '  - <<: { package-ecosystem: "npm" }',
  );

  assert.throws(
    () => assertTypeScriptDependabotPolicy("version: [\n"),
    /Flow sequence in block collection/,
  );
  assert.throws(
    () => assertTypeScriptDependabotPolicy(duplicateKey),
    /Map keys must be unique/,
  );
  assert.throws(
    () => assertTypeScriptDependabotPolicy(mergeKey),
    /each Dependabot update must name a package ecosystem/,
  );
});

test("accepts an alias-backed exact npm ignore policy", () => {
  const anchored = dependabotConfig.replace(
    "    ignore:\n      # Keep the primary compiler",
    "    ignore: &typescript-ignore\n      # Keep the primary compiler",
  );
  const aliased = `${anchored}\n  - package-ecosystem: "npm"\n    directory: "/packages/example"\n    schedule:\n      interval: "weekly"\n    ignore: *typescript-ignore\n`;

  assert.doesNotThrow(() => assertTypeScriptDependabotPolicy(aliased));
});

test("accepts equivalent Dependabot YAML ordering and adjacent keys", () => {
  const reordered = mutateDependabotConfig((updates) => {
    for (const update of updates) {
      const ecosystem = update["package-ecosystem"];
      delete update["package-ecosystem"];
      update.labels = ["dependencies"];
      update["package-ecosystem"] = ecosystem;
    }
  });

  assert.doesNotThrow(() => assertTypeScriptDependabotPolicy(reordered));
});
