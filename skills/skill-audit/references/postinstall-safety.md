# Postinstall Safety Documentation Pattern

This generic reference documents how to explain and audit a safe `postinstall`
script in an npm package without creating false-positive security findings. It
does not describe the GitHub Release executable, which has no install lifecycle.

## When to Use This Pattern

Use this pattern when your npm package:
- Includes a `postinstall` script for user experience/UX purposes
- Does NOT automatically modify system files or install hooks
- Does NOT execute arbitrary code or make untrusted network requests
- Requires user consent before making any changes

## Problem

Security scanning tools (like skill-audit) may flag `postinstall` scripts as potentially malicious because:
1. A target package runs them automatically after `npm install`
2. They can execute arbitrary code
3. Attackers could use them for supply chain attacks

## Solution

Document your postinstall as safe by including these components:

### 1. README.md Section

Add a clear explanation in your README:

```markdown
## About the Postinstall Script

`<package-name>` includes a `postinstall` script that runs automatically after `npm install`. **This script is informational only:**

- ✅ Does NOT automatically install any hooks
- ✅ Does NOT execute any code that could be considered malicious
- ✅ Does NOT make any network requests
- ✅ Does NOT modify any files without user consent
- ✅ Does NOT collect any user data

The script simply displays a banner message prompting users to optionally run `<command> --install-hook` if they want to set up automatic features. Users must manually run this command to activate any hooks.

In CI environments (GitHub Actions, GitLab CI, Jenkins, etc.), the script exits silently without displaying anything.
```

### 2. SECURITY.md File

Create a dedicated security policy:

```markdown
## Security Policy

### Postinstall Script Safety

`<package-name>` includes a `postinstall` script that runs automatically after installation.

**What the postinstall does:**
- Checks environment (CI detection)
- Displays informational messages
- Requires explicit user action for any hooks

**What the postinstall does NOT do:**
- Does NOT automatically install hooks
- Does NOT execute arbitrary code
- Does NOT make untrusted network requests
- Does NOT modify files without consent

The source code is available in `scripts/postinstall.cjs` for audit.
```

### 3. Code Structure

Structure your postinstall to be auditable:

```javascript
// scripts/postinstall.cjs
/**
 * Postinstall script - informational only
 * Does NOT auto-install hooks or execute malicious code
 */

// Exit early in CI
if (process.env.CI === "true") {
  return;
}

// Show optional prompt (requires user action to proceed)
console.log("Run 'your-package --install-hook' to enable automatic features");
```

## Key Principles

1. **Non-blocking**: Script should never block installation
2. **CI-safe**: Exit silently in CI environments
3. **Consent-based**: Never auto-install hooks without user action
4. **Auditable**: Source code should be easily reviewable
5. **Documented**: Clear explanation in README + SECURITY.md

## Files to Include

| File | Purpose |
|------|---------|
| `README.md` | User-facing postinstall explanation |
| `SECURITY.md` | Security policy with postinstall details |
| `scripts/postinstall.cjs` | Well-commented source code |
| `package.json` | Include SECURITY.md in package (npm detects automatically) |

## Validation Checklist

To verify your postinstall is properly documented:

- [ ] README includes "About the Postinstall Script" section
- [ ] SECURITY.md exists with postinstall safety explanation
- [ ] Script exits early in CI environments
- [ ] Script doesn't auto-install hooks
- [ ] Source code is easily auditable (not obfuscated)

## Related

- [npm postinstall best practices](https://docs.npmjs.com/cli/v10/using-npm/scripts#prepost-scripts)
- [OWASP Supply Chain Security](https://owasp.org/www-project-supply-chain-python/)
