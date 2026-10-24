---
name: vux-journey-sweep
description: "Runs a full visual/UX and accessibility sweep of bourbonbook against a live instance: brings up the container, reads admin credentials, dispatches the tester subagent across the user and admin journeys at mobile and desktop viewports, checks docker logs, and collates findings by severity. Use when the user asks to sweep the UI, check accessibility, run a UX regression pass, or invokes /vux-journey-sweep. For photo-analysis field accuracy, use e2e-bottle-test instead."
---

## Context

This is the launcher for the `tester` agent in its journey-sweep mode, mirroring what
`e2e-bottle-test` does for its field-accuracy mode. It exercises the real stack in a real browser, which the in-process
`TestClient` suite cannot do. It is slow and mutates a real local database, so run it only when
asked.

Fixed facts, established once:

- Container lifecycle: `run-docker.sh` at the repo root — stops any existing `bourbonbook`
  container, rebuilds via `make build`, runs it fresh, mounting
  `/Users/aaron/Documents/Development/bourbonbook/data` as `/data` and reading `.env` from there.
- Live URL: `https://bourbonbook.orb.local`. Readiness: `GET /readyz` → `{"status":"ok"}`.
- Faster alternative for template/CSS-only work: `make run_local` on `http://127.0.0.1:8000`. Use
  the container when the change touches the image, migrations, or configuration.
- Admin credentials come from `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` in that `.env`,
  bootstrapped by `identity.py::bootstrap_admin`. **Read them fresh each run**; never hardcode them
  here, and never echo them into the transcript.
- Visual baselines: `tests/visual/baseline/<journey>-<step>-<width>.png`.

## Procedure

### 1. Choose and start the target

Default to the container: run `./run-docker.sh`, then poll
`curl -sk https://bourbonbook.orb.local/readyz` every 2s up to 60s. If it never becomes ready, stop
and report that as the finding — there is nothing to sweep.

If the user asked for a fast iteration on templates or CSS, use `make run_local` instead and say
which target you used.

### 2. Read credentials

Read `DEFAULT_ADMIN_EMAIL` and `DEFAULT_ADMIN_PASSWORD` from the data-dir `.env`. If either is
missing, stop — the admin journeys cannot run, and a partial sweep should be an explicit decision.

Also establish a **non-admin test account** (`test_user_email` / `test_user_password`) for the
profile journey, registering one if none exists. The profile journey must edit that account, not
the admin account, so sweeps stay repeatable.

Registering is only self-service outside production: `EMAIL_VERIFICATION_REQUIRED` defaults to
true, so `/register` lands on `/check-email`, which renders a "Local development link" when
`APP_ENV` is not `production` — follow that link to verify, then log in. Reuse an existing test
account when there is one: `MAX_USERS` defaults to 10, so registering a fresh account on every
sweep will eventually hit the limit.

### 3. Decide scope

Default: all seven journeys, run in this order because later ones depend on earlier state:

1. **Anonymous and auth** — `/login`, `/register`, `/forgot-password`; unauthenticated access to
   `/`, `/profile`, `/admin/users`, `/bottles/new` must redirect to login without leaking content,
   and messages must not reveal whether an account exists. Then log in as admin.
2. **Collection** — `/` and `/collection/compact`: search, sort, card layout, empty states.
3. **Bottle lifecycle** — upload, edit, status and fill level, delete.
4. **Shopping list** — `/shopping-list`: add an item, attach a photo, mark purchased, delete.
5. **Sharing** — enable a share link, check the generated `/shared/{token}` URL anonymously, then
   log back in as admin and disable it.
6. **Profile (test account)** — log in as `test_user_email` and exercise `/profile`, then log back
   in as admin for the admin journey.
7. **Admin** — `/admin/users`, `/admin/catalog`, `/admin/catalog-import`, `/admin/usage`, and
   `/admin/config`, confirming no secret value is ever displayed.

Narrow it when the user names an area, or when a diff clearly touches only one — a change to
`templates/admin/` does not need the shopping-list journey. State the scope you chose and why.

If a baseline is missing for a screen in scope, note it; do not treat it as a failure.

### 4. Dispatch `tester`

Spawn the subagent in the foreground with: `base_url`, the admin credentials, the non-admin test
account credentials for the profile journey, the journey scope, the viewport list (390×844 primary,
1440×900 secondary), the baseline directory, and an `image_path` from `tests/images/` for the
bottle-upload step. Its severity scale and output format are documented in
`~/.agents/agents/tester.md`.

It is read-only by tool list. It cannot fix anything, and it must not be asked to.

### 5. Check container logs

Run `docker logs bourbonbook` and scan for `Traceback`, `ERROR`, `CRITICAL`. This is an independent
signal from the subagent's own verdict — either one finding a real problem counts. Skip when running
against `make run_local`; read that terminal output instead.

### 6. Collate and report

Merge the subagent's findings with anything from the logs and present:

- The verdict: `PASS`, `PASS WITH FINDINGS`, or `FAIL` (any BLOCKER ⇒ FAIL).
- Findings ordered by severity, each with journey, viewport, URL, evidence, and repro steps.
- Visual diffs: which baselines changed and by how much; which screens had no baseline.
- What could not be tested, and why.

**Do not fix anything.** Unlike `e2e-bottle-test`, this skill has no diagnose-and-repair loop —
a UX sweep produces a findings report, and remediation goes to `architect` (if it is a design
question) or `engineer` (if it is a defect) as a separate, reviewable piece of work. Say
which findings you are routing where.

## Ground rules

- Never update a visual baseline as part of a sweep. A changed baseline is a deliberate decision
  that belongs in a PR where a human can see the before and after.
- Never commit anything. If the sweep is part of a larger task, leave the working tree alone.
- Never run against a production or remote deployment unless explicitly told to.
- Docker start/stop/rebuild during this workflow is expected and needs no per-step confirmation —
  it is a disposable local dev container.
- Do not delete user accounts or data the sweep did not create, do not save `/admin/config`, and do
  not trigger `/admin/restart`.
- Vision-model field variance is not a UX finding.
