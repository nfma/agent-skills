# Vulnerability Intelligence Sources

This reference documents the vulnerability-intelligence integrations present in skill-audit and whether each one participates in the current audit path.

## Overview

skill-audit contains clients or maintenance support for five vulnerability-intelligence sources. Only the live OSV dependency path currently participates in ordinary audits.

| Source | Provider | Current integration |
|--------|----------|---------------------|
| CISA KEV | CISA | Maintenance cache and snapshot export only |
| NIST NVD | NIST | Maintenance cache and snapshot export only |
| FIRST EPSS | FIRST | Maintenance cache and snapshot export only |
| GitHub GHSA | GitHub | Query helper present; not wired into ordinary audits |
| OSV.dev | Google | Live dependency lookup; not cached by `--update-db` |

---

## CISA KEV (Known Exploited Vulnerabilities)

### Overview

The CISA Known Exploited Vulnerabilities (KEV) catalog is a list of vulnerabilities that have been exploited in the wild. It is the authoritative source for actively exploited vulnerabilities.

### Details

| Attribute | Value |
|-----------|-------|
| **Full Name** | CISA Known Exploited Vulnerabilities Catalog |
| **Provider** | Cybersecurity and Infrastructure Security Agency (CISA) |
| **URL** | https://www.cisa.gov/known-exploited-vulnerabilities-catalog |
| **API Endpoint** | https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json |
| **Update Frequency** | Daily (around 14:00 UTC) |
| **Max Cache Age** | 1 day |

### Data Format

```json
{
  "cveID": "CVE-2021-44228",
  "vendorProject": "Apache Software Foundation",
  "product": "Apache Log4j",
  "vulnerabilityName": "Apache Log4j2 JNDI features used in configuration, log messages, and parameters do not protect against attacker controlled LDAP and other JNDI related endpoints.",
  "knownRansomwareCampaignUse": "Known"
}
```

### Usage in skill-audit

- `--update-db` downloads the catalog into the maintenance cache
- `--download-offline-db` exports a KEV snapshot
- Ordinary audits do not currently cross-reference dependencies against the KEV cache

---

## NIST NVD (National Vulnerability Database)

### Overview

The NVD is the U.S. government repository of standards-based vulnerability management data. It provides CVSS scores, CWE mappings, and detailed vulnerability descriptions.

### Details

| Attribute | Value |
|-----------|-------|
| **Full Name** | National Vulnerability Database |
| **Provider** | National Institute of Standards and Technology (NIST) |
| **URL** | https://nvd.nist.gov/ |
| **API Endpoint** | https://services.nvd.nist.gov/rest/json/cves/2.0 |
| **Update Frequency** | Daily (around 00:00 UTC) |
| **Max Cache Age** | 1 day |

### Data Format

```json
{
  "id": "CVE-2021-44228",
  "descriptions": [
    {
      "lang": "en",
      "value": "Apache Log4j2 2.0-beta9 through 2.15.0 (excluding security releases 2.12.2, 2.12.3, and 2.3.1) JNDI features used in configuration, log messages, and parameters do not protect against attacker controlled LDAP and other JNDI related endpoints."
    }
  ],
  "metrics": {
    "cvssMetricV31": [
      {
        "cvssData": {
          "baseScore": 10.0,
          "baseSeverity": "CRITICAL"
        }
      }
    ]
  }
}
```

### Usage in skill-audit

- `--update-db` downloads CVEs modified in the preceding 24 hours into the maintenance cache
- `--download-offline-db` exports the same 24-hour snapshot
- Ordinary audits do not currently enrich findings from the NVD cache

---

## FIRST EPSS (Exploit Prediction Scoring System)

### Overview

EPSS provides a probability score (0-100) that a vulnerability will be exploited within the next 30 days. It helps prioritize remediation efforts.

### Details

| Attribute | Value |
|-----------|-------|
| **Full Name** | Exploit Prediction Scoring System |
| **Provider** | FIRST (Forum of Incident Response and Security Teams) |
| **URL** | https://www.first.org/epss/ |
| **API Endpoint** | https://api.first.org/data/v1/epss |
| **Update Frequency** | Daily (around 00:00 UTC) |
| **Max Cache Age** | 3 days |

### Data Format

```json
{
  "data": [
    {
      "cve": "CVE-2021-44228",
      "epss": 0.97,
      "percentile": 0.9998,
      "date": "2024-01-15"
    }
  ]
}
```

### Usage in skill-audit

- `--update-db` downloads EPSS data into the maintenance cache
- `--download-offline-db` exports an EPSS snapshot
- Ordinary audits do not currently enrich findings from the EPSS cache

### EPSS Score Interpretation

| Score | Interpretation |
|-------|---------------|
| 0.0 - 0.1 | Very low exploit probability |
| 0.1 - 0.5 | Low exploit probability |
| 0.5 - 0.9 | Moderate exploit probability |
| 0.9 - 1.0 | High exploit probability |

---

## GitHub Security Advisories (GHSA)

### Overview

GitHub Security Advisories provide vulnerability information specific to the open-source ecosystem, including package ecosystem, affected versions, and patches.

### Details

| Attribute | Value |
|-----------|-------|
| **Full Name** | GitHub Security Advisories |
| **Provider** | GitHub |
| **URL** | https://github.com/advisories |
| **API Endpoint** | https://api.github.com/advisories |
| **Update Frequency** | On-release (varies) |
| **Max Cache Age** | 3 days |

### Data Format

```json
{
  "ghsa_id": "GHSA-xxxx-xxxx-xxxx",
  "cve_id": "CVE-2021-44228",
  "summary": "Remote code injection in Log4j",
  "severity": "critical",
  "published_at": "2021-12-10T00:00:00Z",
  "vulnerabilities": [
    {
      "package": {
        "ecosystem": "Maven",
        "name": "org.apache.logging.log4j:log4j-core"
      },
      "vulnerable_version_range": "< 2.15.0"
    }
  ]
}
```

### Usage in skill-audit

- A GHSA query helper maps advisories into the common record shape
- No production audit or update command currently calls that helper
- GHSA data is therefore not included in ordinary audit findings

---

## OSV.dev (Open Source Vulnerabilities)

### Overview

OSV is an open-source vulnerability database that aggregates vulnerability data from multiple sources and provides a unified API.

### Details

| Attribute | Value |
|-----------|-------|
| **Full Name** | Open Source Vulnerabilities Database |
| **Provider** | Google |
| **URL** | https://osv.dev/ |
| **API Endpoint** | https://api.osv.dev/v1/query |
| **Update Frequency** | On-query (no bulk download) |
| **Max Cache Age** | 7 days |

### Data Format

```json
{
  "id": "OSV-2021-1349",
  "summary": "Remote code execution in Log4j",
  "details": "Apache Log4j2 2.0-beta9 through 2.15.0...",
  "aliases": ["CVE-2021-44228"],
  "published": "2021-12-10T00:00:00Z",
  "affected": [
    {
      "package": {
        "name": "org.apache.logging.log4j:log4j-core",
        "ecosystem": "Maven"
      },
      "ranges": [
        {
          "type": "SEMVER",
          "events": [
            {"introduced": "2.0.0"},
            {"fixed": "2.15.0"}
          ]
        }
      ]
    }
  ]
}
```

### Usage in skill-audit

- Dependency scanning can query OSV on demand when its configured scanner path requires it
- `--update-db` does not bulk-download or cache OSV data
- OSV CLI manages its own offline database separately from skill-audit's maintenance cache

---

## Cache Management

### Cache Location

`skill-audit` resolves its cache root in this order:

1. the absolute path in `SKILL_AUDIT_CACHE_DIR`, when set;
2. the platform user cache directory: `~/Library/Caches/skill-audit` on
   macOS, `$XDG_CACHE_HOME/skill-audit` or `~/.cache/skill-audit` on Linux,
   and `%LOCALAPPDATA%\skill-audit` on Windows;
3. an explicit runner-temporary path in CI. The repository update workflow
   sets `SKILL_AUDIT_CACHE_DIR` to `$RUNNER_TEMP/skill-audit-cache`.

Feed data is stored below the resolved root's `feeds/` directory, and update
metrics are stored in `metrics.json` at the root. Package-local and repository
cache paths are not supported.

### Cache Update

```bash
# Manual update
skill-audit --update-db

# Update specific sources
skill-audit --update-db --source kev epss nvd
```

These caches support feed maintenance and snapshot export. Ordinary audit reports do not read them or emit cache-staleness warnings.

---

## Source Prioritization

The codebase includes a merge/prioritization helper for a future combined-intelligence path. It is not wired into ordinary audits today.

When multiple sources provide the same CVE:

| Priority | Source | Use Case |
|----------|--------|----------|
| 1 | CISA KEV | Actively exploited vulnerabilities |
| 2 | GHSA | Package-specific details |
| 3 | NVD | CVSS scores, CWE mappings |
| 4 | EPSS | Exploit probability |
| 5 | OSV | Additional ecosystem coverage |

---

## API Rate Limits

| Source | Rate Limit | Notes |
|--------|------------|-------|
| CISA KEV | None | Public dataset |
| NIST NVD | 6 requests/rolling 30s | Requires API key for higher limits |
| FIRST EPSS | 100 requests/day | Public API |
| GitHub GHSA | 5000 requests/hour | Requires authentication for higher limits |
| OSV.dev | 10000 requests/day | Public API |

---

## References

- [CISA KEV Catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)
- [NIST NVD](https://nvd.nist.gov/)
- [FIRST EPSS](https://www.first.org/epss/)
- [GitHub Security Advisories](https://github.com/advisories)
- [OSV.dev](https://osv.dev/)
