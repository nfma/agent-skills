"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");

const skillPath = join(__dirname, "..", "skills", "git-workflow", "SKILL.md");
const skill = readFileSync(skillPath, "utf8");
/** @type {string} */
let stackedSection;

test.before(() => {
  const section = skill
    .split(/^## Stacked pull requests$/m)[1]
    ?.split(/^## /m)[0];
  assert.ok(section, "stacked pull request policy section must exist");
  stackedSection = section;
});

test("keeps direct force-pushes prohibited", () => {
  assert.match(skill, /Never force-push directly/);
  assert.match(
    stackedSection,
    /Never run `git push --force`, `git push -f`, or direct\s+`git push --force-with-lease`/,
  );
});

test("limits the lease exception to the official stack extension", () => {
  assert.match(stackedSection, /official `github\/gh-stack` extension/);
  assert.match(
    stackedSection,
    /Verify `gh extension list` identifies `gh stack` as `github\/gh-stack`/,
  );
  assert.match(
    stackedSection,
    /Allow only `gh stack push` or `gh stack submit` to issue the extension's\s+built-in lease-protected update/,
  );
});

test("requires stack, repository, branch, and worktree guards", () => {
  assert.match(stackedSection, /Require a clean working tree/);
  assert.match(stackedSection, /Run `gh stack view`/);
  assert.match(stackedSection, /listed in\s+the current stack/);
  assert.match(stackedSection, /expected same-repository remote/);
  assert.match(stackedSection, /exclude the\s+repository's default branch/);
});

test("fails closed when the lease is rejected", () => {
  assert.match(
    stackedSection,
    /If a lease check rejects an update, stop and inspect the remote state/,
  );
  assert.match(
    stackedSection,
    /Never\s+retry with a broader force option or bypass the lease/,
  );
});
