---
name: fixture
description: A fixture used to validate released skill-audit behavior.
context:
  reads:
    - user_goal
  requires:
    - explicit_user_intent
  writes:
    - audit_summary
  confirmation: on-risk
---

# Fixture

Use the fixture safely.
