# Risk Scoring Methodology

This reference documents how skill-audit calculates risk scores for AI agent skills.

## Overview

skill-audit uses a weighted scoring system that aggregates findings across multiple categories to produce a single risk score between 0 and 10.

## Score Classification

| Level | Score Range | Action |
|-------|-------------|--------|
| ✅ Safe | 0 | Deploy or install without concerns |
| ⚠️ Risky | 0.1 - 3.0 | Review findings; acceptable for low-risk use cases |
| 🔴 Dangerous | 3.1 - 7.0 | Fix issues before deployment; significant risks present |
| ☠️ Malicious | 7.1 - 10.0 | DO NOT USE; contains critical vulnerabilities |

## Scoring Algorithm

### Base Score Calculation

```
Total Score = Σ(Category Scores)
Category Score = Σ(Finding Score × Finding Weight)
```

### Finding Weights by Category

| Category | Base Weight | Description |
|----------|-------------|-------------|
| **Specification** | 1.0 | SKILL.md format, frontmatter, structure |
| **Prompt Injection (ASI01)** | 2.5 | Patterns that could override system instructions |
| **Secrets/Leaks (ASI04)** | 2.0 | Hardcoded API keys, tokens, credentials |
| **Code Execution (ASI05)** | 2.5 | Dynamic code execution without sandboxing |
| **Exfiltration (ASI02)** | 2.0 | Data leakage to untrusted endpoints |
| **Behavioral Manipulation (ASI09)** | 1.5 | Attempts to manipulate agent behavior |
| **PII Detection (ASI03)** | 1.5 | Personally Identifiable Information exposure |
| **PII Exfiltration (PEX)** | 2.0 | PII being sent to external endpoints |
| **Compliance** | 1.5 | Missing compliance-related documentation indicators |
| **Dependency Vulnerabilities** | 1.0 | CVEs in skill dependencies |

### Severity Multipliers

Each finding also has a severity multiplier:

| Severity | Multiplier | Example |
|----------|------------|---------|
| info | 0.1 | Minor spec issues |
| low | 0.3 | Non-critical findings |
| medium | 0.6 | Moderate risk |
| high | 1.0 | Significant risk |
| critical | 2.0 | Active exploitation possible |

## Finding Score Formula

```
Finding Score = Base Weight × Severity Multiplier × Count
```

### Example Calculations

#### Example 1: Single Hardcoded API Key

```
Finding: ASI04-01 (Hardcoded API Key)
Base Weight: 2.0
Severity: high (1.0)
Count: 1

Score = 2.0 × 1.0 × 1 = 2.0 (Risky)
```

#### Example 2: Multiple Prompt Injection Patterns

```
Finding: ASI01 (Prompt Injection)
Base Weight: 2.5
Severity: high (1.0)
Count: 3

Score = 2.5 × 1.0 × 3 = 7.5 (Malicious)
```

#### Example 3: Mixed Findings

```
Findings:
- SPEC-01 (missing SKILL.md): 1.0 × 0.6 × 1 = 0.6
- ASI04-01 (API key): 2.0 × 1.0 × 1 = 2.0
- ASI01-02 (prompt injection): 2.5 × 1.0 × 1 = 2.5

Total: 0.6 + 2.0 + 2.5 = 5.1 (Dangerous)
```

## Threshold Configuration

### Default Thresholds

| Threshold | Value | Use Case |
|-----------|-------|----------|
| `--threshold` | 3.0 | Default blocking threshold |
| Warning | 1.0 | Show warning but don't block |

### Custom Thresholds

```bash
# Block only dangerous skills
skill-audit -t 3.0

# Block risky skills too
skill-audit -t 1.0

# Report only, no blocking
skill-audit -t 10.0
```

## Score Aggregation Rules

### Category Aggregation

1. **Sum all findings** within each category
2. **Cap category score** at 3.0 per category to prevent single categories from dominating
3. **Sum category caps** to get total score
4. **Apply ceiling** at 10.0

### Cap Example

```
Category Scores Before Cap:
- Security: 8.0 (capped to 3.0)
- PII: 2.5
- Compliance: 1.5

Total: 3.0 + 2.5 + 1.5 = 7.0
```

## Decision Matrix

| Risk Level | Score | Recommended Action |
|------------|-------|-------------------|
| ✅ Safe | 0 | Deploy without concerns |
| ⚠️ Risky | 0.1 - 1.0 | Review minor findings |
| ⚠️ Risky | 1.1 - 3.0 | Address findings before production |
| 🔴 Dangerous | 3.1 - 5.0 | Fix critical issues |
| 🔴 Dangerous | 5.1 - 7.0 | Significant rework required |
| ☠️ Malicious | 7.1 - 10.0 | Do not use |

## Factors Not Included in Score

The following are tracked but don't affect the numeric score:

- **Maintenance-cache age**: Not consumed by the current audit path or included in its score
- **Spec format warnings**: Advisory, not blocking
- **Performance considerations**: Out of scope for security audit

## Output Interpretation

### JSON Output

```json
{
  "riskScore": 5.2,
  "riskLevel": "dangerous",
  "findings": [
    {
      "id": "ASI04-01",
      "category": "Secrets",
      "score": 2.0,
      "message": "Hardcoded API key detected"
    }
  ],
  "summary": {
    "safe": 0,
    "risky": 2,
    "dangerous": 1,
    "malicious": 0
  }
}
```

### Threshold Exit Codes

| Exit Code | Meaning |
|-----------|---------|
| 0 | Success (below threshold) |
| 1 | Threshold exceeded |

---

## Rationale

### Why These Weights?

| Category | Weight Rationale |
|----------|------------------|
| Prompt Injection (2.5) | Highest - directly compromises agent integrity |
| Code Execution (2.5) | Highest - potential for system compromise |
| Exfiltration (2.0) | High - data breach risk |
| Secrets (2.0) | High - credential compromise |
| PII Exfiltration (2.0) | High - privacy violation |
| Compliance (1.5) | Medium - regulatory risk |
| Behavioral Manipulation (1.5) | Medium - agent behavior corruption |
| PII Detection (1.5) | Medium - privacy exposure |
| Dependencies (1.0) | Lower - requires exploit chain |
| Specification (1.0) | Lower - prevents other checks |

### Why Cap at 10.0?

Prevents score inflation and ensures meaningful differentiation between extremely dangerous skills and moderately dangerous ones.

---

## References

- [OWASP AI Security Top 10](https://owasp.org/www-project-top-ten.html)
- [CVSS 3.1 Specification](https://www.first.org/cvss/v3.1/specification-document)
- [NIST Risk Management Framework](https://csrc.nist.gov/projects/risk-management)
