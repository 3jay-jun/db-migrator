# AGENTS.md - DB Migrator agent entrypoint

This file is the short entrypoint for Codex sessions in this repository.
Keep detailed product, architecture, and execution knowledge in the linked source documents.

## Session Bootstrap

Before changing code, inspect the current task through these files in order:

1. `Codex.md` - detailed Codex working rules and self-review checklist
2. `02-prd.md` - product requirements and v1.0/v1.1 scope
3. `03-Preplan.md` - architecture, domain model, milestones, validation policy
4. `05-plan.md` - implementation roadmap and phase status
5. `docs/exec-plans/active/` - active phase checklist, if any

Use `rg` to find related code and docs before adding new logic. Report the search keywords and result summary when making changes.

## Source Of Truth

- Product scope lives in `02-prd.md`.
- Architecture and domain model live in `03-Preplan.md`.
- Phase status and completion summaries live in `05-plan.md`.
- Detailed active work checklists live only in `docs/exec-plans/active/phase{N}-{name}.md`.
- Completed phase records live in `docs/exec-plans/completed/`.
- CLI reference and generated docs live in `docs/generated/`.
- Sample configs and split product specs live in `docs/product-specs/`.

Do not duplicate business rules across documents. If a rule must be mentioned in multiple places, keep one source document authoritative and make the others point to it.

## Engineering Constraints

- Preserve the v1.0 full migration path and v1.1 incremental migration path as separate flows.
- Keep Core Engine independent from CLI, GUI, rich, FastAPI, WebSocket, or other UI details.
- Keep DBMS-specific SQL dialects inside adapters.
- Process large data through streaming iterators/generators and batch writes; never load a full table into memory.
- Keep checkpoint, resume, retry, validation, and destructive-operation safety behavior covered by tests when touched.
- Never expose passwords, tokens, API keys, or connection secrets in logs, reports, fixtures, or documentation examples.

## Work Flow

For implementation tasks:

1. Read the bootstrap documents relevant to the task.
2. Search for existing behavior with `rg`.
3. Reuse or extend existing code before adding new helpers.
4. If a phase is active, update its checklist as work completes.
5. Run focused tests first, then broader tests when shared behavior changes.
6. Summarize changed files, verification, assumptions, SSOT search, and side effects.

For new phase work, create or confirm `docs/exec-plans/active/phase{N}-{name}.md` before coding.

## Design Preference

Use simple code until the domain has a real changing axis. Introduce interfaces, strategies, factories, or repositories only when they remove meaningful duplication or isolate a concrete dependency.

