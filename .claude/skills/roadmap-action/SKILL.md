---
name: roadmap-action
description: "Implements or completes a single numbered action from the Bourbon Book roadmap in docs/adr/plan.md (A01–A13, P2-xx). Enforces the plan's Required Lifecycle: one action per branch, tracker row set to In Progress, focused tests, make pr-review, commit-bound reviewer/validator gates, draft PR, then tracker row set to Complete with evidence. Use when the user names a roadmap action ID, asks to work the plan, or invokes $roadmap-action."
---

## Context

`docs/adr/plan.md` is the authoritative roadmap. It is ~984 lines with:

- **Action Tracker** table (line ~75): `| ID | Action | Status | Branch | Pull request / completion evidence |`
- **Required Lifecycle for Every Action** (~line 219) — the process this skill enforces
- **Cross-Cutting Requirements** (~line 254) — security, migration, and provider rules that apply
  to every action
- A per-action section (`## A03 — ...`) with **Goal**, **Dependencies**, **Expected Files**,
  **Individual Implementation Instructions**, and **Completion Evidence**

Statuses in use: `Complete`, `In Progress`, `Incomplete`, `Deferred`, `Blocked by <ID>`,
`Retired — no longer required (ADR 0003)`. Never invent a new status word.

Related ADRs live in `docs/adr/`. ADR 0003 retired the benchmark gate — do not resurrect P2-00/P2-01
as prerequisites.

## Procedure

### 1. Identify and validate the action

Read `docs/adr/plan.md` — the tracker row **and** the full per-action section for the requested ID.
Then check, and stop with a clear report if any fails:

- The status is `Incomplete` or `In Progress`. A `Deferred` action cannot begin until its named
  checkpoint is done and the tracker records an approved scope. A `Blocked by X` action cannot begin
  until X is `Complete`. A `Retired` action is not work.
- Every dependency named in the action's **Dependencies** has merged.
- The action's scope is unambiguous. If the plan text and the current code disagree about what
  already exists, report the discrepancy and ask before proceeding — the plan's audit notes can be
  stale.

Never combine two actions in one branch, even if they look trivially related.

### 2. Prepare the branch

```bash
git status --short          # preserve unrelated user work; do not stage it
git fetch origin
git switch -c <branch from the tracker row> origin/<default branch>
```

If the tracker branch already exists, inspect it (`git log`, `git diff`) before reusing it.

Set the tracker row's Status to `In Progress` locally. Do not mark it `Complete` yet.

### 3. Implement

Follow the action's **Individual Implementation Instructions** and touch only the files in its
**Expected Files** list plus their tests. If you must go outside that list, say why in the report.

Prefer extending an existing module over adding a parallel implementation. Apply the plan's
**Cross-Cutting Requirements** — in particular:

- CSRF, authenticated owner scoping, and admin authorization on every new browser route.
- Never log or commit keys, passwords, cookies, tokens, or full authenticated page contents.
- Treat user-, model-, and web-supplied URLs and content as untrusted: validate scheme and host,
  block private/link-local/loopback destinations, cap redirects and response size, prevent DNS
  rebinding and SSRF.
- Keep MSRP, retailer asking price, completed sale, auction result, and user-reported price as
  distinct evidence types; require exact product/release/edition/size matching; store currency and
  observation date.
- Migrations forward-only, tested on both a fresh database and an upgraded legacy-shaped copy.
- Update `.env.example`, admin config, README, Docker/Unraid docs, metrics, and usage accounting
  whenever the action introduces runtime behavior or configuration.
- Deterministic tests never call OpenAI, Ollama, Qdrant, SMTP, or the web.

Use the sibling skills when the action crosses their boundary: `migration-change`,
`pwa-visual-check`, `provider-evaluation`.

### 4. Test and validate

Run focused tests throughout, then:

```bash
make lint
make test
make coverage     # repo-wide floor is 80%; the plan also expects >=80% focused coverage
```

Per the plan's lifecycle step 7, a **separate** validation pass must inspect the final diff — spawn
`bourbonbook-reviewer` for that. Resolve every actionable finding, each with a regression test and a
rerun of the affected checks.

### 5. Full gate and commit

```bash
make pr-review    # lint, coverage, bandit, dependency audit, pr-check, compose, image build
git diff --check
git status --short
```

Stage only files belonging to this action. Commit with a terse action-specific message.

Then run the commit-bound gates from `AGENTS.md`: `bourbonbook-reviewer` must return `PASS` with
`reviewed_commit: <sha>`, and `pr-validator` in local mode must return `PASS` with
`validated_commit: <sha>` — both matching the candidate commit. Any fix means a new candidate commit
and fresh runs of both.

### 6. Draft PR, then update the tracker

Push the branch and open a **draft** PR into the default branch. The body must cover: the change,
root cause, user impact, migrations/configuration, tests, sub-agent validation verdicts with SHAs,
and the `make pr-review` result.

Only after the draft PR exists, update the tracker row to `Complete` with the PR URL and completion
evidence, commit that plan update, and push it to the same branch. Match the evidence style of the
A01/A02 rows.

### 7. Stop

Do not begin the next action in this session. Report which action is next, its branch, its
dependencies, and whether it must wait for this PR to merge.

## Hard stops

- Do not mark an action `Complete` before the draft PR exists.
- Do not merge the PR. Approval is not authorization to merge.
- Do not lower the coverage floor, skip a test, or weaken a check to pass the gate.
- Do not edit `.env`, and do not put real secrets in `.env.example`.
