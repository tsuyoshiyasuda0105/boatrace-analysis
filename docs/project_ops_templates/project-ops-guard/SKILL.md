---
name: project-ops-guard
description: Cross-project operating guardrails for Codex work. Use when starting or resuming any project task that can involve file edits, testing tools, deletions, rollbacks, handoff, or multi-step implementation. Read the project's AGENTS.md and handoff log first, record active files before editing, log concrete failures with prevention steps, list deletion targets before removing anything, list rollback targets before reverting anything, and close all tools and local servers before finishing.
---

# Project Ops Guard

Follow this workflow every time the skill is invoked.

## 1. Read before doing

- Read `AGENTS.md` if it exists.
- Read `docs/handoff.md` if it exists.
- Read `docs/ops_checklist.md` if it exists.
- If they do not exist, create or install them before continuing.

## 2. Claim the work

- Write the current task into `docs/handoff.md`.
- Write the files you expect to edit into `docs/handoff.md`.
- If another active entry already claims the same file, write the conflict-avoidance plan before editing.

## 3. Use skills deliberately

- When another skill is triggered, note the skill name in `docs/handoff.md`.
- If the skill-driven work fails or causes a retry, add a concrete failure entry with:
  - what was attempted
  - the exact failure
  - the root cause
  - the prevention step
  - what rule or checklist item was updated

## 4. Guard dangerous actions

- Before deleting anything, list the exact targets in `docs/handoff.md`.
- Before reverting or discarding anything, list the exact files or settings that will be restored.
- Do not execute deletions or rollbacks until the targets are explicitly recorded and confirmed.

## 5. Track tools and processes

- Record any local server, Playwright run, watcher, helper script, or background process in `docs/handoff.md`.
- Before finishing, stop those tools and clear or update the running-process section.
- Treat an unclosed tool as unfinished work.

## 6. Close with a handoff

- Update completed work.
- Update next actions.
- Update open decisions.
- Update failure log if anything went wrong.
- Run through `docs/ops_checklist.md` before ending.

## References

- Read `references/handoff-fields.md` when you need the expected handoff sections.
- Read `references/failure-log-examples.md` when you need examples of concrete failure logging.
