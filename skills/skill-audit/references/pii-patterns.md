# PII Detection Patterns

This reference documents all 39 Personally Identifiable Information (PII) patterns detected by skill-audit for both Vietnamese and International contexts.

## Overview

skill-audit detects PII across three categories:
- **Vietnam PII**: Patterns specific to Vietnamese identity documents
- **International PII**: Common global PII patterns
- **Secrets**: API keys, tokens, and credentials

## Vietnam PII Patterns

### CCCD (Citizen Identification Card)

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-001 | New CCCD format (12 digits) | `\b\d{12}\b` |
| PII-002 | CCCD with spaces | `\b\d{3}\s?\d{3}\s?\d{6}\b` |

**Detection Context**: Match against Vietnamese address patterns or "CCCD", "Căn cước", "CMND" keywords.

### CMND (Old Identity Card)

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-003 | Old CMND (9 digits) | `\b\d{9}\b` |
| PII-004 | CMND with spaces | `\b\d{3}\s?\d{6}\b` |

### Tax ID (TIN)

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-005 | Vietnam Tax ID (10 digits) | `\b\d{10}\b` |
| PII-006 | Tax ID with prefix | `\b\d{3}\s?\d{3}\s?\d{4}\b` |

### Phone Numbers

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-007 | Vietnam mobile (10 digits) | `\b0\d{9,10}\b` |
| PII-008 | Vietnam phone with +84 | `\b\+84\d{9,10}\b` |

### Bank Account

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-009 | Vietnam bank account | `\b\d{8,16}\b` |
| PII-010 | Bank account with bank code | `\b\d{3}\s?\d{3,11}\b` |

### License Plate

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-011 | Vietnam license plate | `\b\d{2}[A-Z]\d{4,5}\b` |

### BHXH (Social Insurance)

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-012 | Social Insurance ID | `\b\d{10,13}\b` |

### Military ID

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-013 | Military personnel ID | `\b\d{7,12}\b` |

### Passport

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-014 | Vietnam passport | `\b[A-Z]\d{7,8}\b` |

---

## International PII Patterns

### US Social Security Number (SSN)

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-015 | SSN with dashes | `\b\d{3}-\d{2}-\d{4}\b` |
| PII-016 | SSN without dashes | `\b\d{9}\b` (context-dependent) |

### Credit Card

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-017 | Visa | `\b4\d{3}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b` |
| PII-018 | Mastercard | `\b5[1-5]\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b` |
| PII-019 | Amex | `\b3[47]\d{2}[\s-]?\d{6}[\s-]?\d{5}\b` |
| PII-020 | Discover | `\b6011[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b` |

### IBAN (International Bank Account Number)

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-021 | IBAN format | `\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` |

### UK NHS Number

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-022 | NHS number | `\b\d{3}\s?\d{3}\s?\d{4}\b` |

### International Passports

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-023 | US passport | `\b[A-Z]\d{8}\b` |
| PII-024 | EU passport | `\b[A-Z]{2}\d{6,8}\b` |
| PII-025 | UK passport | `\b\d{9}\b` |
| PII-026 | Japan passport | `\b[A-Z]\d{7}\b` |
| PII-027 | Korea passport | `\b[M]\d{8}\b` |

### IP Address

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-028 | IPv4 address | `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b` |
| PII-029 | IPv6 address | `\b([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b` |

### Email Address

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-030 | Email address | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b` |

---

## Secrets Patterns

### API Keys

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-031 | OpenAI API key | `sk-[a-zA-Z0-9]{20,}` |
| PII-032 | Anthropic API key | `sk-ant-[a-zA-Z0-9]{20,}` |
| PII-033 | AWS Access Key ID | `AKIA[0-9A-Z]{16}` |
| PII-034 | GitHub Token | `gh[pousr]_[A-Za-z0-9_]{36,}` |
| PII-035 | Stripe API key | `sk_live_[0-9a-zA-Z]{24}` |

### Private Keys

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-036 | PEM private key | `-----BEGIN (RSA |EC |DSA |OPENSSH) PRIVATE KEY-----` |
| PII-037 | JWT token | `eyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+` |

### Database Connection Strings

| Pattern ID | Description | Regex Pattern |
|------------|-------------|---------------|
| PII-038 | PostgreSQL connection | `postgresql://[^\s]+` |
| PII-039 | MySQL connection | `mysql://[^\s]+` |

---

## PII Exfiltration Detection

skill-audit also detects when PII is being sent to external endpoints:

| Pattern ID | Description |
|------------|-------------|
| PEX-01 | PII in URL query parameters |
| PEX-02 | PII in HTTP POST body |
| PEX-03 | PII in HTTP headers |
| PEX-04 | PII in WebSocket messages |
| PEX-05 | PII sent to analytics endpoints |
| PEX-06 | PII sent to third-party APIs |
| PEX-07 | PII in console.log statements |
| PEX-08 | PII in error messages |
| PEX-09 | PII in localStorage/sessionStorage |
| PEX-10 | PII in cookies |
| PEX-11 | PII in environment variables |

---

## Usage in Audit

When running audit mode, skill-audit automatically scans for these patterns:

```bash
# Full audit with PII detection
skill-audit --mode audit -v

# JSON output for integration
skill-audit --mode audit -j -o audit.json
```

### Sample Finding

```json
{
  "findingId": "PII-001",
  "category": "PII Detection",
  "severity": "high",
  "message": "Vietnam CCCD (Citizen ID) pattern detected",
  "location": "src/user-data.ts:42",
  "matchedPattern": "\\b\\d{12}\\b",
  "context": "const userId = 123456789012;"
}
```

---

## Remediation

1. **Remove hardcoded PII**: Replace with environment variables or config
2. **Use placeholders**: In examples, use fake data like `CCCD_PLACEHOLDER`
3. **Encrypt sensitive data**: If storage is required, use proper encryption
4. **Validate input**: Sanitize user inputs to prevent injection
5. **Audit logs**: Ensure logs don't contain PII

---

## References

- [Vietnam Personal Data Protection Decree](https://chinhphu.vn/)
- [GDPR Article 4 - Personal Data](https://gdpr-info.eu/art-4-gdpr/)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
