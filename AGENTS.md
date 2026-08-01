# Bourbon Book Agent Instructions

## Agent roster

The same three roles are defined for both runtimes. Codex reads `.codex/agents/*.toml`; Claude Code
reads `.claude/agents/*.md`. Names below are equivalent — use whichever form your runtime expects.

| Role | Codex | Claude Code |
|------|-------|-------------|
| Design, planning, and architecture docs | — | `senior-architect` |
| Independent design critique | — | `architecture-critic` |
| Implementation | (primary session) | `senior-engineer` |
| Independent code review | `bourbonbook_reviewer` | `bourbonbook-reviewer` |
| PR authoring and CI triage | — | `pr-manager` |
| PR validation and approval | `pr_validator` | `pr-validator` |
| End-to-end UX and accessibility testing | — | `vux-tester` |

Claude Code also has `e2e-bottle-tester`, invoked only by the `e2e-bottle-test` skill.

`vux-tester` and `e2e-bottle-tester` are complementary and not interchangeable: `vux-tester` sweeps
whole journeys, viewports, accessibility, and console health across the app; `e2e-bottle-test`
measures photo-analysis field accuracy against `tests/images/ImageTestValidation.md`. Neither treats
vision-model output variance as a defect.

Both require the Playwright MCP server defined in `.mcp.json`. `pr-manager` and `pr-validator` use
the GitHub MCP when it is connected and authorized, and fall back to `gh` with `GH_TOKEN` mapped
from `GITHUB_PAT`. Approval remains a commit-pinned `gh api` call in `pr-validator` only.

### Design-to-delivery chain

For any change that is not a small, obvious fix:

1. `senior-architect` reviews the request against the existing design and the current code, then
   drafts a proposal: problem, constraints, options, recommendation, design, phasing, documentation
   impact, risks.
2. `architecture-critic` reviews that proposal read-only and returns `APPROVE` or `REVISE` with
   evidence-backed findings.
3. The architect revises and re-submits. **Maximum three critic rounds** — if a substantive
   disagreement survives round three, the architect writes up both positions and escalates to Aaron
   rather than proceeding or looping.
4. On `APPROVE`, the architect records the work as a new Action Tracker row plus an action section
   in `docs/adr/plan.md`, and writes a new ADR with `Status: Proposed` if a decision was made.
5. **GATE: Aaron approves the plan.** The architect presents the recorded plan — acceptance
   criteria, work items, verification methods, migrations, documentation impact, risks — and stops.
   No implementation begins without an explicit go-ahead. This is the last cheap place to change
   direction; everything after it costs code.
6. The architect hands the action ID, branch, and dependencies to `senior-engineer`, which
   implements it under the `$roadmap-action` skill and the PR sequence below.
7. Once both commit-bound verdicts pass, `pr-manager` writes the PR body, opens the draft, triages
   CI check failures, routes review comments, and updates the tracker row. `pr-validator` in remote
   approval mode is the only agent permitted to approve, and nothing may merge automatically —
   a merge to the default branch triggers `docker-publish.yml` and tags a release.
8. **After the change merges**, the architect updates the as-built documentation named in the plan's
   documentation-impact list (HLDD, C1–C4 views, `docs/architecture/components/`) and flips any new
   ADR from `Proposed` to `Accepted`.

The as-built invariant: `docs/architecture/` describes only what is checked in today. Proposed work
lives in `docs/adr/plan.md` and in `Proposed` ADRs — never in the HLDD or the C-views.

### Escalation rules

Every agent in the chain follows these. They exist because an agent that hits an unclear situation
will invent a decision rather than admit ambiguity — halting is what prevents that.

| Condition | Action |
|---|---|
| A reviewer requests changes 3× on the same artifact | Halt. Summarize both positions and escalate to Aaron. Do not start a 4th round. |
| A CI check or local gate fails 3× on the same work item | Halt. Dump the failing logs and escalate. Do not attempt a 4th fix. |
| The engineer wants to deviate from the plan | Stop the work item. Return to `senior-architect` for a plan amendment. Do not improvise inside the action's scope. |
| Implementation reveals the plan is wrong or the requirement is ambiguous | Halt the work item and escalate to the architect, then to Aaron. Do not guess the intent. |
| Architect and critic deadlock on a substantive point | Write both options into a decision-request ADR with `Status: Proposed`; Aaron picks. |
| An agent needs a capability it has no tool for | Say so and stop. Never route around a missing tool by asking another agent to perform a restricted action. |

Escalating is a successful outcome, not a failure. A halted work item with a clear question costs
far less than a merged wrong assumption.

## Project skills

Use the available skill that matches the work:

- Use `$roadmap-action` when implementing or completing an action from `plan.md`.
- Use `$migration-change` for model, schema, Alembic, migration-bootstrap, or persistent-data
  changes.
- Use `$pwa-visual-check` for templates, CSS, JavaScript, forms, responsive UI, uploads, icons,
  manifests, or service-worker behavior.
- Use `$provider-evaluation` for Ollama, OpenAI, pricing search, embeddings, Qdrant, prompts,
  structured outputs, fallbacks, or provider usage accounting.

Use multiple skills when a change crosses these boundaries. Follow each selected skill's workflow
in addition to the review and validation sequence below.

These four skills are defined in `.claude/skills/<name>/SKILL.md`. `.claude/skills/e2e-bottle-test`
additionally drives a live browser through the real photo-upload pipeline; run it only when asked.

## PR review, validation, and approval

Before opening or updating a pull request for implementation changes:

1. Complete the implementation and focused tests in the primary session.
2. Use `bourbonbook_reviewer` for preliminary review while iterating, and resolve every actionable
   finding in the primary session.
3. Create the final candidate commit in the primary session.
4. Spawn `bourbonbook_reviewer` against that exact commit. A final `PASS` must include
   `reviewed_commit: <sha>` matching the candidate commit, with no intended scope left uncommitted.
5. Spawn `pr_validator` in local validation mode against the same commit. It must run
   `make pr-review` and a final `PASS` must include `validated_commit: <sha>` matching the candidate.
6. Treat reviewer or validator `FAIL` findings as actionable work for the primary session. Any fix
   requires a new candidate commit and fresh commit-bound runs of both agents.
7. Open or update the pull request as a draft only after both final agents return `PASS` for the
   same candidate commit. Include both verdicts, commit SHAs, and `make pr-review` in the description.
8. After that commit is pushed, invoke `pr_validator` again in remote approval mode with the
   repository, pull-request number, expected head SHA, and both commit-bound verdicts. It must wait
   for the complete expected GitHub check set and bind any approval to that exact head commit.
9. The remote validator may approve only when every requirement passes and the authenticated
   GitHub identity is allowed to approve. It must return `BLOCKED` rather than claim success when
   GitHub forbids self-approval or the credential lacks review permission.
10. Approval is not authorization to merge the pull request.

Preserve unrelated user changes throughout validation. Never stage files merely because test or
build tooling generated them.
