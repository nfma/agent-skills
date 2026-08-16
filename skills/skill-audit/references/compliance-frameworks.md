# Compliance Frameworks

This reference documents the optional heuristics skill-audit uses to flag missing compliance-related documentation in AI agent skills. These checks do not validate legal compliance and are not a legal determination.

## Overview

skill-audit can compare skill documentation with indicators associated with three regulatory frameworks:

| Framework | Region | Effective Date | Risk Levels |
|-----------|--------|---------------|-------------|
| Vietnam AI Law 2026 | Vietnam | March 1, 2026 | minimal, limited, high, unacceptable |
| EU AI Act | European Union | August 2024 (phased) | minimal, limited, high, unacceptable |
| GDPR | European Union | May 2018 | minimal, limited, high, unacceptable |

---

## Vietnam AI Law 2026

### Overview

Vietnam's Law on Artificial Intelligence No. 134/2025/QH15 was passed on December 10, 2025 and took effect on March 1, 2026. The checks below are documentation indicators implemented by skill-audit; they are not an exhaustive statement of the law or proof that a skill is compliant.

### Heuristic Checks

| Requirement ID | Documentation indicator | Finding severity |
|----------------|-------------|------------------------------|
| VN-AI-001 | **Data Localization**: Personal data collected in Vietnam must be stored and processed within Vietnam | high |
| VN-AI-002 | **User Consent**: Obtain explicit consent before collecting/processing personal data | high |
| VN-AI-003 | **Transparency**: AI systems must disclose they are AI-generated to users | limited |
| VN-AI-004 | **Human Oversight**: Maintain human oversight for high-risk AI decisions | high |
| VN-AI-005 | **Data Minimization**: Collect only data necessary for the declared purpose | limited |
| VN-AI-006 | **Right to Explanation**: Provide explanations for AI-driven decisions | limited |
| VN-AI-007 | **Bias Prevention**: Implement measures to prevent discriminatory outcomes | high |

### Risk Classification

| Risk Level | Description | Requirements |
|------------|-------------|--------------|
| **minimal** | AI with no decision-making impact | Basic registration |
| **limited** | AI with limited human oversight | Disclosure + consent |
| **high** | AI affecting legal rights, health, safety | Full impact assessment |
| **unacceptable** | Social scoring, real-time biometric surveillance | Prohibited |

### Skill-Specific Checks

For Claude Code skills, compliance checks focus on:

1. **Data Handling**: Does the skill send Vietnamese user data outside Vietnam?
2. **Consent Mechanisms**: Does the skill prompt for consent before data collection?
3. **Transparency**: Does the skill disclose AI nature in outputs?
4. **Bias Prevention**: Does the skill avoid discriminatory prompts?

---

## EU AI Act

### Overview

The European Union's AI Act (Regulation (EU) 2024/1689) is the world's most comprehensive AI regulation. It classifies AI systems by risk and imposes corresponding obligations.

### Key Requirements

| Requirement ID | Documentation indicator | Finding severity |
|----------------|-------------|------------------------------|
| EU-AI-001 | **Risk Assessment**: Conduct risk assessment before deployment | high |
| EU-AI-002 | **Data Governance**: Ensure data quality and representativeness | limited |
| EU-AI-003 | **Technical Documentation**: Maintain detailed technical records | limited |
| EU-AI-004 | **Record Keeping**: Log system operations for traceability | limited |
| EU-AI-005 | **Transparency**: Provide sufficient information to users | limited |

### Risk Classification

| Risk Level | Examples | Requirements |
|------------|----------|--------------|
| **unacceptable** | Social scoring, biometric categorization, manipulation | Prohibited |
| **high** | Employment, critical infrastructure, law enforcement | Strict requirements |
| **limited** | Chatbots, emotion recognition | Transparency obligations |
| **minimal** | Spam filters, recommendation systems | No obligations |

### Skill-Specific Checks

1. **Prohibited Practices**: Does the skill implement prohibited AI techniques?
2. **High-Risk Classification**: Does the skill affect employment, healthcare, or legal rights?
3. **Transparency**: Does the skill disclose AI-generated content?
4. **Data Quality**: Is training data properly documented?

---

## GDPR (General Data Protection Regulation)

### Overview

The EU's GDPR (Regulation (EU) 2016/679) is the foundational data protection regulation affecting any entity processing EU residents' personal data.

### Key Requirements

| Requirement ID | Documentation indicator | Finding severity |
|----------------|-------------|------------------------------|
| GDPR-001 | **Lawful Basis**: Identify legal basis for processing (consent, contract, legitimate interest) | high |
| GDPR-002 | **Data Subject Rights**: Support rights to access, rectify, erase, port data | high |
| GDPR-003 | **Privacy by Design**: Implement data protection from conception | limited |
| GDPR-004 | **DPIA**: Conduct Data Protection Impact Assessment for high-risk processing | high |
| GDPR-005 | **Breach Notification**: Have procedures for notifying authorities within 72 hours | high |
| GDPR-006 | **International Transfers**: Ensure adequate protection for data transfers outside EU | high |

### Key Principles

| Principle | Description |
|-----------|-------------|
| **Lawfulness, Fairness, Transparency** | Process data legally and openly |
| **Purpose Limitation** | Collect for specified, explicit purposes only |
| **Data Minimization** | Collect only what's necessary |
| **Accuracy** | Keep data accurate and up-to-date |
| **Storage Limitation** | Don't keep data longer than necessary |
| **Integrity & Confidentiality** | Ensure appropriate security |
| **Accountability** | Demonstrate compliance |

### Skill-Specific Checks

1. **Consent**: Does the skill obtain valid consent for data processing?
2. **Data Minimization**: Does the skill collect only necessary data?
3. **Security**: Are appropriate security measures in place?
4. **Third-Party Transfers**: Is data transferred to non-EU countries with safeguards?

---

## Heuristic Compliance Output

When skill-audit runs optional compliance heuristics, it produces findings like:

```json
{
  "findingId": "VN-AI-001",
  "category": "Compliance",
  "framework": "Vietnam AI Law 2026",
  "severity": "high",
  "message": "Data localization requirement not met",
  "description": "Skill sends user data to external endpoints without ensuring data stays in Vietnam",
  "recommendation": "Implement data localization or obtain explicit user consent for international transfers"
}
```

### Finding Severity Mapping

| Framework | minimal | limited | high | unacceptable |
|-----------|---------|---------|------|---------------|
| Vietnam AI Law 2026 | Info | Warning | Error | Critical |
| EU AI Act | Info | Warning | Error | Critical |
| GDPR | Info | Warning | Error | Critical |

---

## Remediation Guidelines

### For Vietnam AI Law

1. **Data Localization**: Store Vietnamese user data on servers within Vietnam
2. **Consent**: Add consent prompts before collecting personal information
3. **Transparency**: Include "AI-generated" disclaimers in outputs
4. **Bias Audits**: Regularly audit prompts for discriminatory content

### For EU AI Act

1. **Classification**: Determine risk level of the AI system
2. **Documentation**: Maintain technical documentation per Annex IV
3. **Transparency**: Provide required information to users
4. **Human Oversight**: Implement human-in-the-loop for high-risk decisions

### For GDPR

1. **Lawful Basis**: Document the legal basis for each processing activity
2. **Data Subject Rights**: Implement mechanisms for rights requests
3. **DPIA**: Conduct assessment for high-risk processing
4. **Security**: Implement appropriate technical and organizational measures

---

## References

### Vietnam AI Law 2026
- [Vietnam Government legal database - Law No. 134/2025/QH15](https://vanban.chinhphu.vn/?docid=216334&orggroupid=1&pageid=27160)

### EU AI Act
- [EUR-Lex - AI Act](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [EU AI Office](https://artificialintelligenceoffice.eu/)

### GDPR
- [EUR-Lex - GDPR](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [EDPB Guidelines](https://www.edpb.europa.eu/)
