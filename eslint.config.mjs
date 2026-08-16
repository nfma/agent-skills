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
  {
    files: ["scripts/update-codex-mcp-config.cjs"],
    rules: {
      // The requested path is absolute and non-root, then canonicalized before use.
      "security/detect-non-literal-fs-filename": "off",
    },
  },
  {
    files: [
      "tests/hf-mcp-filter.test.js",
      "tests/update-codex-mcp-config.test.js",
    ],
    rules: {
      // The test creates and removes only paths beneath its private mkdtemp root.
      "security/detect-non-literal-fs-filename": "off",
    },
  },
);
