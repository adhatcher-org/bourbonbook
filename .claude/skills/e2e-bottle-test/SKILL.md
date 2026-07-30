---
name: e2e-bottle-test
description: "Runs a real browser-driven end-to-end test of bourbonbook's photo-upload pipeline against a live local Docker container: starts run-docker.sh, uploads every photo in tests/images/ through the actual /bottles/new form via the e2e-bottle-tester subagent (Playwright MCP), checks docker logs for runtime errors, and — only for hard failures (crashes/5xx/log errors/completely-failed analysis), never for ordinary field-value mismatches — automatically diagnoses, fixes, and retests, capped at 3 cycles. Use when the user asks to run/re-run the bottle-upload e2e test, or invokes /e2e-bottle-test."
---

## Context

This exercises the real stack — Docker, networking, a real browser, real Ollama vision analysis —
which the existing `tests/*.py` suite (FastAPI in-process `TestClient`) never touches. It is slow
(each bottle upload waits on live vision-model inference) and mutates a real local database, so
only run it when asked.

Fixed facts about this repo, established once so this doesn't need re-deriving each run:

- Container lifecycle script: `run-docker.sh` (repo root) — stops any existing `bourbonbook`
  container, rebuilds via `make build`, and runs it fresh, mounting
  `/Users/aaron/Documents/Development/bourbonbook/data` as `/data` and reading
  `/Users/aaron/Documents/Development/bourbonbook/data/.env`.
- Live URL: `https://bourbonbook.orb.local` (OrbStack container DNS). Health:
  `GET /readyz` → `{"status":"ok"}` once the app and DB are ready.
- Admin login already exists on first boot from `DEFAULT_ADMIN_EMAIL` /
  `DEFAULT_ADMIN_PASSWORD` in that same `.env` file (bootstrapped by
  `bourbonbook/identity.py::bootstrap_admin`) — read those two values fresh each run rather than
  hardcoding them here, in case they change.
- Fixture: `tests/images/ImageTestValidation.md`, images alongside it in `tests/images/`.
- Log check target: `docker logs bourbonbook`.

## Procedure

### 1. Parse the fixture

Read `tests/images/ImageTestValidation.md`. It's a sequence of `## <bottle label>` sections, each
followed by `key: value` lines until the next `## ` header or end of file. Build one record per
section:
- `bottle_label`: the `## ` heading text
- `image_path`: resolve the filename out of the `image: ![...](<filename>)` line to an absolute
  path under `tests/images/`
- `expected`: every other `key: value` line as a dict (skip the `image:` line itself)

There should be 8 records (one per `.jpeg` in `tests/images/`). If the count doesn't match the
number of image files in the directory, stop and report the mismatch rather than guessing.

### 2. Start the container

Run `./run-docker.sh` from the repo root (Bash, foreground, this rebuilds the image so it always
tests current code). Then poll `curl -sk https://bourbonbook.orb.local/readyz` every 2s, up to
60s total, until it returns `{"status":"ok"}`. If it never becomes ready, treat that itself as a
hard failure and go straight to step 5's diagnose/fix path (skip step 3/4 — there's nothing to
test yet).

### 3. Run the browser test

Spawn the `e2e-bottle-tester` subagent (foreground — its report drives the next decision) with a
prompt containing: `base_url=https://bourbonbook.orb.local`, the admin email/password read from
`.env` in step-0, and the full list of records from step 1 (image paths + expected dicts). Its
required output format is documented in `.claude/agents/e2e-bottle-tester.md` — a `## HARD_FAILURES`
section (either `none` or an itemized list) followed by per-bottle field-by-field results.

### 4. Check docker logs

Run `docker logs bourbonbook` and scan for `Traceback`, `ERROR`, `CRITICAL`. Correlate rough
timestamps with when the test ran if the log is large. This is a second, independent signal from
the subagent's own `HARD_FAILURES` verdict — either one finding a real problem counts.

### 5. Decide

**No hard failures** (subagent reported none AND logs are clean): present the full per-bottle,
per-field results table to the user as-is. Explicitly call out that SOFT-MISMATCH entries
(fill_level, wording variance, msrp not matching exactly) are known vision-model/price-search
variance, not bugs — do not treat them as failures needing action. Stop here.

**Hard failures found** — run this loop, cycle count starts at 1, hard cap 3:

a. `docker stop bourbonbook`.
b. Spawn a **`Plan`** built-in agent (read-only + Bash) with: the exact hard-failure details from
   the subagent report, the relevant `docker logs` excerpt, and a pointer to start from
   `bourbonbook/main.py`'s `add_bottle` handler and `bourbonbook/analysis.py`. Ask it to produce a
   root-cause diagnosis and a concrete, specific remediation plan (not implement anything).
c. Spawn a **`general-purpose`** agent with that plan, instructed to implement the fix, then run
   `pytest -xvs` and `ruff check .` to confirm no regressions before reporting back. If it can't
   get tests green, it should say so rather than claim success.
d. Go back to step 2 (rebuild/restart via `run-docker.sh`) and step 3 (retest with the same
   `e2e-bottle-tester` subagent — this retest step is deliberately the same subagent re-invoked,
   not a separate definition).
e. If hard failures persist after 3 total cycles: stop, do not attempt a 4th change, and report to
   the user the full history — what each cycle diagnosed, what each cycle changed, and the
   current failing state — so they can decide how to proceed by hand.

## Ground rules

- Field-value mismatches against `ImageTestValidation.md` are never by themselves grounds to
  change application code — only the subagent's declared HARD_FAILURES and docker log errors are.
  Chasing exact-match on vision-model OCR output or fill-level percentage is not this test's job.
- Docker start/stop/rebuild during this workflow is expected and does not need per-step
  confirmation — it's a disposable local dev container.
- Never commit anything as part of this skill. If cycles produced a fix, leave it as an
  uncommitted working-tree change and let the user review and commit it themselves.
