# AI execution workflow

## Purpose

Use a compact, verified execution state instead of carrying a growing transcript
between Codex tasks. This is a work-management convention; it does not replace
the repository's architecture, security, testing, or authorization rules.

## Model routing

- Use **Terra Medium** for normal implementation and verification work.
- Use **Luna Low** only for narrow, mechanical work with named files, an expected
  result, a known verification command, and no design decision.
- Use **Sol Medium** for read-only planning when a task needs a material
  architecture or public-contract decision, changes a security boundary, needs
  a migration or deployment strategy, or remains unclear after focused initial
  investigation.
- Do not use a model or reasoning level above Sol Medium for this workflow.

## Triage and routing

Choose the model when starting a task. Check the concrete risk signals above;
the number of files or components alone does not require Sol. If the request is
obviously mechanical and fully specified, start Luna Low. For ordinary or
uncertain work, start Terra Medium. Start Sol Medium only when a listed risk
requires planning, then hand its result to Terra for implementation.

Do not create a separate Luna task just to classify another task. An instruction
file cannot switch the model of a running Codex task; when the chosen model is
unsuitable, record the reason and use a new task with the appropriate model.

## Handoff artifacts

The planning task produces an **Implementation Brief**. The implementing task
receives the Brief and the current **Execution State**, rather than a full prior
conversation.

Keep both artifacts concise. They must not contain credentials, raw connection
URLs, sensitive literals, full unredacted logs, or speculative history. Facts in
the state require a source: an inspected file, a command result, a test result,
or an explicit user decision.

### Implementation Brief

Use this structure:

```text
Goal:
Non-goals:
Accepted decisions and constraints:
Steps: each step names affected paths, contract changes, and success criteria.
Verification: commands or checks required for each relevant step.
Risks and escalation conditions:
```

The planner works read-only unless the user separately authorizes an edit. It
must inspect the applicable repository instructions and existing behavior before
issuing the Brief.

### Execution State

Use this structure and replace obsolete entries instead of appending a diary:

```text
Goal:
Phase: implementation | verification | blocked | complete
Accepted decisions:
Completed, with evidence:
Next concrete action:
Affected paths:
Verification: command/check -> current result
Blocker or decision needed:
```

The implementation task reads the relevant source again; the state is a compact
handoff, not a substitute for current repository evidence. Update facts after
a verified environment outcome and decisions after an explicit user decision.
A failed command remains represented as a short, actionable fact until resolved;
do not carry the entire old output forward.

## Task flow

1. For a task requiring planning, create a Sol Medium task with read-only scope.
   Its sole durable output is the Implementation Brief and initial Execution
   State.
2. Start a Terra Medium implementation task from the intended checkout and Git
   state. Give it the two artifacts, not the planner transcript. Before editing,
   confirm its `HEAD`, worktree, and uncommitted changes contain the expected
   starting code. It follows repository instructions, edits only within the
   Brief, and runs proportional checks.
3. On a contradiction with live code, an unclear verification failure, or a
   material design decision, return the compact state plus fresh evidence to Sol
   Medium. Make sure that task can inspect the current code and diff. Do not
   repeatedly retry or widen scope without that decision.
4. For independent implementation tasks, use separate worktrees. For sequential
   work, continue in the same task or a shared permanent worktree. If a new task
   must use code from a managed worktree, use Codex Handoff to move the prior
   task and its changes to the local checkout, then start the new task from that
   current Git state and verify it sees the changes. Passing the Brief alone
   does not transfer code. Do not commit solely to transfer state.

## Calibration

After several representative tasks, compare the chosen model, total usage if
available, elapsed time, accepted result, and rework. Adjust routing only from
observed outcomes; the model split and token savings are not guaranteed.

## Completion

The final task report names changed files, checks actually run and their result,
checks not run, and remaining risks. The project-specific definition of done and
authorization rules remain authoritative.
