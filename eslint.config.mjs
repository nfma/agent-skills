import eslint from "@eslint/js";
import security from "eslint-plugin-security";
import sonarjs from "eslint-plugin-sonarjs";
import globals from "globals";
import typescriptEslint from "typescript-eslint";

export default typescriptEslint.config(
  {
    ignores: ["node_modules/**", "vendor/**", "skills/**/node_modules/**"],
  },
  eslint.configs.recommended,
  ...typescriptEslint.configs.recommended,
  security.configs.recommended,
  sonarjs.configs.recommended,
  {
    files: ["**/*.{js,cjs,mjs,ts,tsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: globals.node,
      sourceType: "module",
    },
    rules: {
      "security/detect-object-injection": "off",
    },
  },
  {
    files: ["**/*.cjs", "mcp/bin/hf-mcp-filter.js", "tests/**/*.js"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  {
    files: ["scripts/audit-skills.mjs", "scripts/check-shell-quality.mjs"],
    rules: {
      // Every dynamic path is constrained by lexical and canonical root checks.
      "security/detect-non-literal-fs-filename": "off",
    },
  },
);
