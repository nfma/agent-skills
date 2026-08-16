# Antigravity Review Contract

Apply this contract whenever a task-specific review prompt points here.

## Independence and posture

Work alone and do not consult other agents. Be adversarial, not contrarian. Try to falsify the change with realistic inputs, states, timing, and integration behavior. Report only issues that survive inspection and would change an engineering decision. Do not invent objections or reward disagreement.

Treat risk hypotheses in the task-specific prompt as probe directions, not conclusions. Any or all may prove false, and an empty findings list is an acceptable and useful result.

## Read-only boundary

Treat every path named in the task-specific read-only set as immutable. Report what should change without editing, creating, moving, deleting, or regenerating anything in that set or its repository.

## Evidence

Prefer direct inspection, a file-and-line citation, or a command and its output over memory. If command execution is unavailable under plan and sandbox mode, put the claim in `QUESTIONS` and name the exact read-only validation command. Do not promote an unverified, command-dependent claim to a required change.

## Response

Copy the task prompt's sentinel exactly as the first line of the response. Then return these two sections and nothing else:

### FINDINGS

For every finding, provide all six fields:

- severity;
- a falsifiable claim stated so it could be proven wrong;
- exact evidence, using a file and line or a command and its output;
- a realistic failure scenario naming who encounters it, when, and what happens;
- impact;
- the smallest change that resolves it.

### QUESTIONS

Put unsupported suspicions and every command-dependent claim that could not be verified here. An empty section is valid.
