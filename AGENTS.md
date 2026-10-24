# Bourbon Agent Instructions

## Agent roster

Bourbon Book uses the shared, global agent set defined once in `~/.agents/agents/` and reused
across all repos (Golf League, Bourbon Book, and any future one). There is a single orchestrator;
no other agent dispatches another.

| Role | Global definition | Notes |
|------|--------------------|-------|
| Orchestration -- the only agent that dispatches the others, owns task-tracking state | `coordinator` | Routes every request through the full chain below |
| Design, planning, and architecture docs | `architect` | Returns its proposal to the coordinator; never self-dispatches the critic |
| Independent design critique | `architect-critic` | Read-only; returns APPROVE/REVISE to the coordinator |
| Implementation | `engineer` | Reads this file for stack/commands/invariants; reports evidence to the coordinator |
| End-to-end testing -- UX/accessibility sweeps and the narrow photo-analysis field-accuracy check | `tester` | Report-only; capability-aware (see below) |
| Pre-PR review + PR authoring/opening | `pr-reviewer` | Reviews security/format/dependency/LLM-safety *before* opening the PR; opens it only if clean |

**Runtime binding.** These are canonical, capability-described `.md` files, not tied to one tool
vocabulary. Claude Code maps their described capabilities to its own tools (Bash, Read, Write,
the Playwright MCP for browser automation, the GitHub MCP or `gh` for PR actions). Codex, OpenCode,
or Cline map the same capabilities to whatever they expose natively. `reasoning_tier` values
(`high`/`medium`/`low`) map to whichever models each runtime calls its top/mid/fast tier -- for
Claude that's Opus/Sonnet/Haiku. If a runtime lacks a described capability (e.g. no browser
automation), the agent says so and reports that check as not run rather than skipping silently.

`tester` covers what used to be two separate Claude Code agents: it sweeps whole journeys,
viewports, accessibility, and console health, **and** it can run the narrower photo-analysis
field-accuracy check against `tests/images/ImageTestValidation.md` when a work item asks for that
specific verification method. Name which one a work item needs -- they are not interchangeable,
and neither treats vision-model output variance as a defect.

`pr-reviewer` uses the GitHub MCP when connected and authorized, and falls back to `gh` with
`GH_TOKEN` mapped from `GITHUB_PAT`. It never prints, logs, or persists that token.

### Design-to-delivery chain

For any change that is not a small, obvious fix, the coordinator drives this sequence -- see the
global `coordinator.md` for the mechanics; this section states only what's specific to this repo:

1. `architect` reviews the request against the existing design and the current code (reading this
   file plus `docs/architecture/`, `docs/adr/`), then drafts a proposal: problem, constraints,
   options, recommendation, design, phasing, documentation impact, risks. Returns it to the
   coordinator -- it does not hand off to the critic itself.
2. Coordinator dispatches `architect-critic`, which reviews read-only and returns `APPROVE` or
   `REVISE` with evidence-backed findings.
3. On `REVISE`, coordinator routes findings back to `architect`, which revises and resubmits.
   **Maximum three critic rounds** -- if a substantive disagreement survives round three, the
   coordinator writes up both positions and escalates to Aaron rather than proceeding or looping.
4. On `APPROVE`, `architect` records the work as a new Action Tracker row plus an action section in
   `docs/adr/plan.md`, and writes a new ADR with `Status: Proposed` if a decision was made.
5. **GATE: Aaron approves the plan.** The coordinator presents the recorded plan -- acceptance
   criteria, work items, verification methods, migrations, documentation impact, risks -- and
   stops. No implementation begins without an explicit go-ahead. This is the last cheap place to
   change direction; everything after it costs code.
6. Coordinator hands the action ID, branch, and dependencies to `engineer`, which implements it
   under the `$roadmap-action` skill (when the runtime supports named skills) and this file's PR
   sequence.
7. Coordinator dispatches `tester` against the engineer's candidate for every work item's named
   verification method. Failures route back to `engineer`; capped at three rounds per finding,
   same escalation rule.
8. Once testing is clean, coordinator dispatches `pr-reviewer`, which reviews for correctness,
   security, format, dependencies, and LLM/provider-usage safety **before** anything is opened.
   Clean review -> it pushes and opens the draft PR itself via `pr-gate push` / `pr-gate
   pr-create` (the only sanctioned path; direct `git push` and `gh pr create` are blocked for
   agents) and writes the body from the plan and diff. Actionable findings -> returned to the
   coordinator, routed to `engineer`, re-tested, and reviewed again -- capped at three rounds.
   `pr-reviewer` is read-mostly and may not commit, so the `docs/adr/plan.md` tracker row is
   flipped to `Complete` by `engineer` on the coordinator's instruction, not by the reviewer.
9. Nothing merges automatically. A merge to the default branch triggers `docker-publish.yml` and
   tags a release, so that stays a deliberate human action. If Aaron authored the change, GitHub
   will not let him approve his own PR on the platform -- that's a separate, later step from
   `pr-reviewer`'s job, which ends at opening the PR clean.
10. **After the change merges**, `architect` updates the as-built documentation named in the plan's
    documentation-impact list (HLDD, C1-C4 views, `docs/architecture/components/`) and flips any
    new ADR from `Proposed` to `Accepted`.

The as-built invariant: `docs/architecture/` describes only what is checked in today. Proposed work
lives in `docs/adr/plan.md` and in `Proposed` ADRs -- never in the HLDD or the C-views.

### Escalation rules

Every agent in the chain follows these, enforced by the coordinator. They exist because an agent
that hits an unclear situation will invent a decision rather than admit ambiguity -- halting is
what prevents that.

| Condition | Action |
|---|---|
| A reviewer requests changes 3x on the same artifact | Halt. Coordinator summarizes both positions and escalates to Aaron. Do not start a 4th round. |
| A CI check or local gate fails 3x on the same work item | Halt. Dump the failing logs and escalate. Do not attempt a 4th fix. |
| The engineer wants to deviate from the plan | Stop the work item. Coordinator returns it to `architect` for a plan amendment. Do not improvise inside the action's scope. |
| Implementation reveals the plan is wrong or the requirement is ambiguous | Halt the work item; coordinator escalates to `architect`, then to Aaron. Do not guess the intent. |
| Architect and critic deadlock on a substantive point | Write both options into a decision-request ADR with `Status: Proposed`; Aaron picks. |
| An agent needs a capability it has no tool for | Say so and stop. Never route around a missing capability by asking another agent to perform a restricted action. |

Escalating is a successful outcome, not a failure. A halted work item with a clear question costs
far less than a merged wrong assumption.

## Stack

- Python >=3.12, FastAPI + Starlette, SQLAlchemy 2.x, Alembic, Jinja2, SQLite.
- uv + make for dependency/task management. Never `pip install`; never hand-edit `uv.lock`
  (`uv add` / `uv lock`).
- Lint/format: ruff (line length 100, rules E,F,I,UP,B,SIM, target py312).
- Providers: Ollama (the default, local vision), OpenAI, and LiteLLM (an OpenAI-compatible
  gateway), pluggable via `ANALYSIS_PROVIDER`. Pricing is local-first: SQLite catalog exact
  match -> optional Qdrant fuzzy match (>=0.82 on vector and difflib score) -> only then a
  grounded web search, with write-back. That grounded search is dispatched per provider, not
  always OpenAI: with the default `ANALYSIS_PROVIDER=ollama` it is Ollama Cloud's
  `/api/web_search` (`bourbonbook/ollama_search.py`). Qdrant is a rebuildable candidate
  generator, never the source of truth.
- Runtime: Docker/Unraid, `/data` persistence, non-root, **single Uvicorn worker / single SQLite
  writer** (do not propose multi-worker or concurrent-writer designs without a new ADR naming the
  consequence).

## Commands

- Test: `pytest` in `tests/`, coverage floor 80% branch coverage on `bourbonbook`.
- Security: `bandit` via `make security`. Dependency audit: `make dependency-check`.
- Full pre-PR gate: `make pr-review` -- lint/format, branch coverage, Bandit, dependency/lock
  audit, repository integrity and migration checks, Compose validation, production image build.
- Individual: `make format`, `make lint`, `make test`, `make coverage`.
- Install/sync the locked dependencies: `make install` (`uv sync --frozen`).
- Build the production image: `make build`. Build the Compose service only: `make build-local`.
- **Reaching a running instance** (what browser testing needs): `./run-docker.sh` brings up the
  container at `https://bourbonbook.orb.local`, with `/readyz` as the readiness probe; or
  `make run_local` runs it directly at `http://127.0.0.1:8000` for fast template/CSS iteration.
  Admin credentials come from `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` in the data-dir
  `.env`; the profile journey needs a separate non-admin account so it does not mutate the admin's.

## Paths

- Architecture docs: `docs/architecture/` (HLDD + C1-C4 views + component docs under
  `docs/architecture/components/`) -- **as-built only**; never edited to describe unmerged work.
- Decision records: `docs/adr/NNNN-<slug>.md` -- new record when a decision is made; supersede,
  never delete or rewrite an accepted one.
- Roadmap/tracker: `docs/adr/plan.md` -- Confirmed Decisions, Cross-Cutting Requirements, Required
  Lifecycle, and the Action Tracker table.

## Invariants

**Always read `$bourbonbook-invariants` first** when the runtime supports named skills -- it is the
single source of truth for the architectural and security constraints every agent designs,
implements, tests, and reviews against. Overturning one requires a new ADR, never a judgment call
inside a change. In a runtime without named-skill support, treat this section plus the summary
below as that same source of truth:

- Baseline (`docs/adr/0001-current-architecture-baseline.md`): FastAPI + Jinja server-rendered,
  SQLite + SQLAlchemy + Alembic, one Docker container on Unraid, all state under `/data`, single
  worker/writer, no SPA framework, no build step.
- Pricing tiers and Qdrant's non-authoritative role: `docs/adr/0002-local-first-pricing-catalog.md`.
- Model roles are a fixed operator decision, not benchmark-gated: `docs/adr/0003-...`.
- The LiteLLM gateway as a third analysis provider: `docs/adr/0006-litellm-gateway-provider.md`.
- Owner scoping and CSRF protection on every authenticated route; admin routes stay behind the
  admin boundary. A provider/network/model failure must degrade to the manual path, never a 500 or
  data loss.
- Migrations: forward-only, idempotent, SQLite-compatible; must work on a fresh DB, a stamped
  pre-Alembic DB, and an already-versioned DB.
- Never place secrets, API keys, prompts, or bottle/user data in code, tests, logs, or evidence.
  `.env` is real and off-limits; only `.env.example` gets new keys, placeholder values only.
- Tests must never contact OpenAI, Ollama, Qdrant, SMTP, or the live web -- injected fakes only.

## Project skills

When the runtime supports named skills, use the one matching the work in addition to the chain
above:

- `$roadmap-action` when implementing or completing an action from `plan.md`.
- `$fastapi-route-change` for any route, handler, guard, CSRF, owner-scoping, form-parsing, or
  rate-limiting change in `bourbonbook/main.py`.
- `$migration-change` for model, schema, Alembic, migration-bootstrap, or persistent-data changes.
- `$pwa-visual-check` for templates, CSS, JavaScript, forms, responsive UI, uploads, icons,
  manifests, or service-worker behavior.
- `$provider-evaluation` for Ollama, OpenAI, pricing search, embeddings, Qdrant, prompts,
  structured outputs, fallbacks, or provider usage accounting.
- `$adr-authoring` when recording, narrowing, or superseding an architectural decision.
- `$c4-diagram-update` for the as-built documentation pass -- HLDD, C1-C4 views, component docs,
  and the rendered Mermaid SVGs.
- `$e2e-bottle-test` / `$vux-journey-sweep` are the two `tester` verification modes described
  above, when the runtime exposes them as invocable skills rather than direct prompts.

Use multiple skills when a change crosses these boundaries. In a runtime without named-skill
support, `architect` and `engineer` apply this same judgment inline from this file.

## PR review and opening (pr-reviewer's job)

### PR facts

The coordinator and `pr-reviewer` read these from this file; they are stated nowhere else.

- **Repo:** `adhatcher-org/bourbonbook`. Default branch `main`.
- **Branch convention:** `<type>/<slug>`, where type is `codex`, `chore`, `docs`, `feature` or
  `fix`. For a tracked action, use the branch named in that `docs/adr/plan.md` tracker row rather
  than inventing one.
- **Expected PR checks:** `ci / Test and build`; `analyze / Analyze (actions)`,
  `analyze / Analyze (javascript-typescript)`, `analyze / Analyze (python)` and `CodeQL`; and
  `claude-review`. CI runs lint, tests, coverage, security, the dependency audit, the PR-readiness
  check and the container build through the shared `adhatcher-org/shared-workflows` reusable
  workflow, so those arrive as the single `ci` check rather than separately.
- **Pushing and opening a PR:** agents may not run `git push` or `gh pr create` directly. `pr-gate`
  is the only sanctioned path, and it refuses unless a pre-PR review PASS is recorded for the exact
  commit being pushed. The user's own terminal is not gated.
- **Merging has release consequences:** `docker-publish.yml` triggers on CI success and tags a
  release, so merging stays a deliberate human action. GitHub will not let Aaron approve a PR he
  authored, so platform approval is a separate, later step.

Before `pr-reviewer` opens anything:

1. `engineer`'s candidate must be commit-bound (a real SHA, not UNCOMMITTED) with nothing intended
   left uncommitted.
2. `tester` must have returned a clean result (or non-blocking findings the coordinator explicitly
   accepted) against that exact commit.
3. `pr-reviewer` runs `make pr-review` and its own security/format/dependency/LLM-safety pass
   against that commit. Any actionable finding goes back to the coordinator, not into a PR.
4. Only on a clean pass does it push the branch (without amending the reviewed commit), write the
   PR body from the plan and diff -- including what was tested, the review result, and
   `make pr-review`'s outcome -- and open the draft PR.
5. It reports the reviewed commit's SHA and the PR URL to the coordinator, and stops there.
   `pr-reviewer` is read-mostly and may not commit, so the `docs/adr/plan.md` tracker-row update
   (`Complete`, with the PR URL) is made by `engineer` on the coordinator's instruction.
6. Approving the PR on GitHub and merging it are both separate, later, human actions -- never part
   of this agent's job. If Aaron authored the change, expect GitHub to require a different reviewer
   identity for that platform-level approval.

Preserve unrelated user changes throughout. Never stage files merely because test or build tooling
generated them.
