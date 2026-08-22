/**
 * @typedef {{ name: string, compatibility: unknown }} SkillCompatibility
 * @typedef {{ skill: string, reason: string }} CompatibilityOmission
 * @typedef {{ errors: string[], approvedOmissions: CompatibilityOmission[] }} CompatibilityPolicyResult
 */

/**
 * @param {unknown} omissionPolicy
 * @returns {{ errors: string[], omissions: Map<string, CompatibilityOmission> }}
 */
function parseOmissionPolicy(omissionPolicy) {
  /** @type {string[]} */
  const errors = [];
  /** @type {Map<string, CompatibilityOmission>} */
  const omissions = new Map();

  if (!Array.isArray(omissionPolicy)) {
    errors.push("compatibilityOmissions must be an array");
    return { errors, omissions };
  }

  for (const [index, rawEntry] of omissionPolicy.entries()) {
    if (rawEntry === null || typeof rawEntry !== "object") {
      errors.push(`compatibilityOmissions[${index}] must be an object`);
      continue;
    }

    const entry = /** @type {{ skill?: unknown, reason?: unknown }} */ (
      rawEntry
    );
    if (typeof entry.skill !== "string" || entry.skill.trim() === "") {
      errors.push(
        `compatibilityOmissions[${index}].skill must be a non-empty string`,
      );
      continue;
    }
    const skill = entry.skill.trim();
    if (omissions.has(skill)) {
      errors.push(`duplicate compatibility omission for skill: ${skill}`);
      continue;
    }
    if (typeof entry.reason !== "string" || entry.reason.trim() === "") {
      errors.push(
        `compatibility omission for ${skill} must include a non-empty reason`,
      );
      continue;
    }
    omissions.set(skill, { skill, reason: entry.reason.trim() });
  }

  return { errors, omissions };
}

/**
 * @param {SkillCompatibility} skill
 * @param {Map<string, CompatibilityOmission>} omissions
 * @param {CompatibilityPolicyResult} result
 */
function evaluateSkill(skill, omissions, result) {
  const omission = omissions.get(skill.name);
  if (skill.compatibility === undefined) {
    if (omission) {
      result.approvedOmissions.push(omission);
    } else {
      result.errors.push(
        `${skill.name} omits compatibility without a deliberate-omission policy entry`,
      );
    }
    return;
  }

  if (
    typeof skill.compatibility !== "string" ||
    skill.compatibility.trim() === ""
  ) {
    result.errors.push(
      `${skill.name} must declare compatibility as a non-empty string`,
    );
    return;
  }
  if (omission) {
    result.errors.push(
      `compatibility omission for ${skill.name} is stale because the skill now declares compatibility`,
    );
  }
}

/**
 * Require every discovered skill either to declare a non-empty compatibility
 * string or to carry a justified deliberate-omission policy entry.
 *
 * @param {SkillCompatibility[]} skills
 * @param {unknown} omissionPolicy
 * @returns {CompatibilityPolicyResult}
 */
export function evaluateCompatibilityPolicy(skills, omissionPolicy) {
  const parsedPolicy = parseOmissionPolicy(omissionPolicy);
  /** @type {CompatibilityPolicyResult} */
  const result = {
    errors: parsedPolicy.errors,
    approvedOmissions: [],
  };
  const skillNames = new Set(skills.map(({ name }) => name));

  for (const skill of skills) {
    evaluateSkill(skill, parsedPolicy.omissions, result);
  }

  for (const skill of parsedPolicy.omissions.keys()) {
    if (!skillNames.has(skill)) {
      result.errors.push(
        `compatibility omission refers to an undiscovered skill: ${skill}`,
      );
    }
  }

  return result;
}
