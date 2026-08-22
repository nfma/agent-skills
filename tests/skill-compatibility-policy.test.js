"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

/** @type {typeof import("../scripts/skill-compatibility-policy.mjs")} */
let policy;

test.before(async () => {
  policy = await import("../scripts/skill-compatibility-policy.mjs");
});

test("accepts declared compatibility and a justified deliberate omission", () => {
  const result = policy.evaluateCompatibilityPolicy(
    [
      { name: "portable", compatibility: "Requires Node.js 24." },
      { name: "instruction-only", compatibility: undefined },
    ],
    [
      {
        skill: "instruction-only",
        reason: "Instruction-only policy with no additional runtime.",
      },
    ],
  );

  assert.deepEqual(result, {
    errors: [],
    approvedOmissions: [
      {
        skill: "instruction-only",
        reason: "Instruction-only policy with no additional runtime.",
      },
    ],
  });
});

test("rejects an unapproved missing compatibility declaration", () => {
  const result = policy.evaluateCompatibilityPolicy(
    [{ name: "silent", compatibility: undefined }],
    [],
  );

  assert.deepEqual(result.approvedOmissions, []);
  assert.deepEqual(result.errors, [
    "silent omits compatibility without a deliberate-omission policy entry",
  ]);
});

test("rejects stale and unknown deliberate omissions", () => {
  const result = policy.evaluateCompatibilityPolicy(
    [{ name: "declared", compatibility: "Requires Python." }],
    [
      { skill: "declared", reason: "No longer true." },
      { skill: "missing", reason: "No such skill." },
    ],
  );

  assert.deepEqual(result.errors, [
    "compatibility omission for declared is stale because the skill now declares compatibility",
    "compatibility omission refers to an undiscovered skill: missing",
  ]);
});

test("rejects malformed, duplicate, and unjustified omission entries", () => {
  const result = policy.evaluateCompatibilityPolicy(
    [{ name: "duplicate", compatibility: undefined }],
    [
      { skill: "duplicate", reason: "First reason." },
      { skill: "duplicate", reason: "Second reason." },
      { skill: "no-reason", reason: " " },
      null,
    ],
  );

  assert.deepEqual(result.approvedOmissions, [
    { skill: "duplicate", reason: "First reason." },
  ]);
  assert.deepEqual(result.errors, [
    "duplicate compatibility omission for skill: duplicate",
    "compatibility omission for no-reason must include a non-empty reason",
    "compatibilityOmissions[3] must be an object",
  ]);
});

test("rejects empty and non-string compatibility declarations", () => {
  const result = policy.evaluateCompatibilityPolicy(
    [
      { name: "empty", compatibility: " " },
      { name: "structured", compatibility: ["Node.js"] },
    ],
    [],
  );

  assert.deepEqual(result.errors, [
    "empty must declare compatibility as a non-empty string",
    "structured must declare compatibility as a non-empty string",
  ]);
});

test("requires an explicit omission policy array", () => {
  const result = policy.evaluateCompatibilityPolicy(
    [{ name: "declared", compatibility: "Requires Git." }],
    undefined,
  );

  assert.deepEqual(result, {
    errors: ["compatibilityOmissions must be an array"],
    approvedOmissions: [],
  });
});
