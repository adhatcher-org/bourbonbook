---
name: adr-authoring
description: "Writes, numbers, and maintains Bourbon Book Architecture Decision Records in docs/adr/. Covers when a decision warrants an ADR, the established section structure, the Proposed to Accepted lifecycle, and the supersede-never-edit rule including how to narrow or retire an earlier decision. Use when recording an architectural decision, changing an existing one, or invoking $adr-authoring."
---

## Context

ADRs live in `docs/adr/NNNN-<slug>.md`, zero-padded to four digits, sequential. Current records:

| ID | Decision | Status |
|---|---|---|
| 0001 | Current architecture baseline | Accepted |
| 0002 | Local-first pricing catalog with optional Qdrant fuzzy match | Accepted |
| 0003 | Fixed local model selection, no benchmark acceptance gate | Accepted |

`docs/adr/plan.md` is **not** an ADR — it is the roadmap tracker that happens to live in the same
directory. Do not renumber around it.

## When an ADR is required

Write one when the decision is **hard to reverse or expensive to undo**:

- It overturns or narrows an invariant in the `bourbonbook-invariants` skill.
- It changes a data contract, a persisted schema shape, or a URL contract.
- It adds a runtime dependency or an external service.
- It changes the deployment or process model.
- It picks between genuinely competing approaches where the loser was viable.

Do **not** write one for: an implementation detail with an obvious right answer, a refactor that
preserves behavior, a bug fix, or a change already covered by an existing ADR or a Confirmed
Decision in `plan.md`. Check both before writing — a duplicate decision record is worse than none,
because it creates two places to look and eventually two answers.

## Structure

Follow the shape 0001–0003 already use. Title line, then metadata, then sections:

```markdown
# ADR NNNN: <Imperative, specific title>

Status: Proposed
Date: YYYY-MM-DD

<One paragraph placing this ADR relative to the others: what it narrows, supersedes,
or extends, with inline links. Name the modules and migrations it concerns.>

## Context

## Decision

## Rationale

## Consequences

## Alternatives Considered

## Supersession Criteria

## Cross-links
```

Section rules that matter:

- **Context** states the forces, not the answer. Include the constraints that made this hard —
  single writer, local-first, one container — and the evidence from the code or from production
  behavior. A reader in a year should understand why the obvious option was not obvious.
- **Decision** is numbered, specific, and testable. "Use SQLite as the durable cache keyed by
  normalized `(product_key, size_key)`" — not "improve pricing." Name the modules, tables, and
  configuration keys it governs.
- **Consequences** must include the ones you dislike. An ADR that lists only benefits is a pitch,
  not a record. State what becomes harder, what is now locked in, and what operational burden it
  adds.
- **Alternatives Considered** needs real options with real reasons they lost. A straw man here is
  the most common ADR failure, and `architecture-critic` is instructed to look for it.
- **Supersession Criteria** is what makes the record maintainable: state the conditions under which
  a future ADR should revisit this. 0003 does this well — it names the hardware and provider
  assumptions that, if they change, reopen the decision.

## Lifecycle

1. **`Status: Proposed`** — written by `senior-architect` alongside the plan, before implementation.
   The critic reviews it as part of the proposal.
2. **`Status: Accepted`** — flipped in the post-merge documentation pass, once the code that
   implements the decision has merged. Update the `Date` only if the decision itself changed.
3. **`Status: Superseded by ADR NNNN`** — set when a later ADR replaces it. Add the link.
   **Never delete the file and never rewrite the Decision section.** The record of what was decided
   and later abandoned is the point.

For a partial change, prefer **narrowing** over superseding: the new ADR says which parts of the
earlier one it replaces, and the earlier one stays `Accepted` with a cross-link. 0002 narrows 0001;
0003 narrows 0001 and retires two roadmap actions. Follow that pattern.

When an ADR retires roadmap work, mark the affected `plan.md` tracker rows **Retired** with a
pointer to the ADR rather than deleting the rows — 0003 and the P2-00/P2-01 rows show the
convention.

## Cross-linking

Every ADR links to the ADRs it relates to, and the reverse links must be added too. Also update:

- `docs/architecture/hldd.md` — the metadata block at the top lists each ADR by role.
- `docs/architecture/components/README.md` — the "See also" list.
- The relevant C-view headers, which cite their governing ADRs.

A new ADR that nothing links to will not be found when it matters.

## Hard stops

- Never edit the Decision, Rationale, or Alternatives of an `Accepted` ADR. Supersede it.
- Never reuse or renumber an ADR ID.
- Never mark an ADR `Accepted` before the implementing change has merged.
- Never write an ADR that contradicts an invariant without stating the consequence of overturning it
  and getting Aaron's decision.
- Never record a decision in an ADR that belongs in `plan.md` as an action, or vice versa: ADRs
  record *why*, the tracker records *what and when*.
