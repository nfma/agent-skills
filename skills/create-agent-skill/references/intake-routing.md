# Intake and routing

## Intake: three answers, then infer the rest

Ask these in one concise interaction when the answers are not already present:

1. **Outcome and examples:** What repeatable job should the skill perform? Give one representative request and the output that would count as good.
2. **Scope and boundaries:** Who uses it, when should it trigger, and what close-looking request must not trigger it?
3. **Evidence and constraints:** What SOP, chat, script, skill, API, or artifact proves the workflow? Name required capabilities, permissions, and the canonical destination repository.

Infer ordinary implementation details. Ask follow-ups only for decisions that change architecture, safety, trigger boundaries, ownership, or measurable success.

Before routing, write:

- a one-sentence job statement;
- representative positive and near-miss prompts;
- observable success and failure checks;
- required inputs, outputs, effects, capabilities, and permissions;
- canonical owner and source repository;
- constraints that belong in the skill versus constraints local to the current task.

## Inventory sequence

Search in this order:

1. canonical shared-skill repository;
2. project-local discovery roots;
3. user-level installed roots;
4. additional roots explicitly supplied by the user or host.

Resolve every root and discovered `SKILL.md` to its physical path. Deduplicate symlinked or copied discovery entries before judging popularity or ownership.

Search by:

- outcome and capability vocabulary;
- realistic trigger and near-miss phrases;
- inputs, output artifacts, and side effects;
- referenced products, standards, APIs, file types, and domain entities;
- resource filenames and executable purposes;
- owner, provenance, and maintenance location.

The inventory helper provides lexical evidence, not a semantic verdict. Read plausible matches before routing.

## Route with evidence

| Route     | Choose when                                                                   | Required output                                         |
| --------- | ----------------------------------------------------------------------------- | ------------------------------------------------------- |
| `use`     | An existing skill already covers the job and boundary                         | Identify it, cite matching behavior, and create nothing |
| `extend`  | Missing behavior belongs to the same job, trigger family, and owner           | Patch the canonical source and add regression cases     |
| `compose` | Orthogonal skills can exchange explicit artifacts without merging ownership   | Define the handoff contract and use both skills         |
| `create`  | The job has a distinct trigger boundary, durable procedure, or ownership need | Explain why nearby skills do not own it                 |

Do not create merely because:

- the requested folder name is absent;
- a model failed once;
- an existing description uses different vocabulary;
- the workflow can be completed with a short one-off prompt;
- the task is an installer or wrapper around volatile product behavior.

One successful baseline does not automatically block a skill whose durable value is consistency, governance, private procedure, or permission control. State that value explicitly and test it with discriminating checks.

## Routing record

Record:

```text
job:
candidate skills and physical paths:
overlap evidence:
route: use | extend | compose | create
reason:
owner and canonical destination:
positive trigger:
near-miss:
success checks:
```
