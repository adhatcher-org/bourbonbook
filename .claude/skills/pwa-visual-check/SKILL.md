---
name: pwa-visual-check
description: "Workflow for Bourbon Book front-end changes — Jinja templates, app.css, app.js, forms, responsive layout, photo uploads, icons, the web manifest, and the service worker. Covers the design tokens, the two breakpoints, service-worker cache-version bumping, and how to actually look at the result in a browser instead of guessing. Use when editing anything under bourbonbook/templates/ or bourbonbook/static/, or invoking $pwa-visual-check."
---

## Context

Fixed facts about the front end:

- **Server-rendered Jinja**, no build step and no framework. Templates in
  `bourbonbook/templates/` (`base.html` is the shell; partials are `_`-prefixed: `_mobile_nav.html`,
  `_collection_header.html`, `_compact_grid.html`; admin templates in `templates/admin/`, email
  templates in `templates/email/`).
- **One stylesheet**: `bourbonbook/static/app.css`, written as dense single-line rule groups. Match
  that style — do not reformat existing lines, and do not introduce a preprocessor or a CDN.
- **One script**: `bourbonbook/static/app.js`, loaded `defer` from `base.html`.
- **Design tokens** live in `:root` in `app.css`: `--ink`, `--surface`, `--surface-2`, `--paper`,
  `--muted`, `--amber`, `--amber-bright`, `--green`, `--red`, `--line`, `--radius`, `--shadow`,
  `--ease`. Dark theme only (`color-scheme:dark`). **Never hardcode a hex that a token already
  covers.**
- **Two breakpoints**: `@media(max-width:900px)` (tablet — grid drops to 2 columns, auth splits) and
  `@media(max-width:620px)` (phone — mobile nav appears, single-column forms). There is no third.
- **Typography**: headings use Georgia serif; body uses the `BookSans` local stack; **form inputs,
  selects, textareas, search, and price/quantity fields use self-hosted `AtkinsonEdit`
  (Atkinson Hyperlegible Next) at `1.05rem` / `1.45` line height** — this is a deliberate
  accessibility decision (plan.md Confirmed Decision 2). Buttons are excluded. WOFF2 files live in
  `bourbonbook/static/fonts/`.
- **PWA**: `static/manifest.webmanifest` (standalone, `#0d0c0b` theme), `static/icon.svg`
  (`any maskable`), and `static/sw.js`.
- **Service worker**: `const CACHE = 'bourbon-book-vN'` with a `SHELL` array precached on install;
  old caches are deleted on activate; fetch only serves same-origin GETs under `/static/` or
  `/manifest.webmanifest`, cache-first.
- **iPhone-first**: `viewport-fit=cover`, `apple-mobile-web-app-capable`, and
  `env(safe-area-inset-bottom)` on the mobile nav. The photo input deliberately has **no**
  `capture="environment"` — that was removed so the iPhone Photo Library chooser appears
  (plan.md A01). Do not add it back.

## Procedure

### 1. Locate the real markup

Find the template that owns the element and check whether it is shared (`base.html`, a `_partial`)
before editing. A change in `base.html` or `app.css` touches every page — say so in your report.

### 2. Make the change

- Reuse existing class names and tokens. Add a new rule group at the end of `app.css` in the same
  compact style, or extend the matching existing group.
- **Every interactive target ≥44px** (the codebase uses 44–48px minimums; `min-height:44px` /
  `min-height:48px` appear throughout). Phone-width overrides must not shrink a target below that.
- **Keep the focus story**: `:focus-visible` styling exists on menus, buttons, and fields. Never
  set `outline:0` without providing a visible replacement.
- Preserve `.skip-link`, `.sr-only`, `aria-hidden` on decorative layers, and semantic elements
  (`<fieldset>/<legend>` on the status and rating pickers, `<label>` wrapping every input).
- Respect the `@media(prefers-reduced-motion:reduce)` block — new animations must be covered by it.
- Forms: every POST form needs its `csrf_token` hidden input. Never remove one.
- If you add or rename a file in `SHELL` — or change `app.css`, `app.js`, `icon.svg`, or the
  manifest in a way clients must pick up — **bump the `CACHE` version in `sw.js`**
  (`bourbon-book-v6` → `v7`). Forgetting this is the most common bug in this area: users keep the
  stale asset indefinitely.

### 3. Look at it

Do not ship a visual change you have not seen rendered.

Local, fastest:

```bash
make run_local     # http://127.0.0.1:8000, reload enabled
```

Container, closest to production (rebuilds the image, mounts real `/data`):

```bash
./run-docker.sh    # then https://bourbonbook.orb.local ; readiness: GET /readyz
```

Then check, at minimum:

- **Phone width (≤620px)**: mobile nav visible and clear of the home indicator, single-column form
  grid, headings not clipped, cards legible, no horizontal scroll.
- **Tablet (≤900px)** and **desktop**: grid column counts and the sticky editor bar.
- **The specific flow you touched** end to end — for `/bottles/new` and the analysis pipeline, the
  real check is the `e2e-bottle-test` skill, which drives a live browser through upload → analysis →
  edit page.
- **PWA install**: if you touched the manifest, service worker, or icon, install/reload as a
  standalone app and confirm the new asset actually arrives (a stale `CACHE` version will hide it).

### 4. Test and verify

Front-end changes still need server-side tests: assert the rendered markup in the relevant
`tests/test_*.py` (route tests use the in-process FastAPI `TestClient`) — e.g. that the field is
present, the CSRF token is rendered, an unauthorized user cannot reach the page.

```bash
make lint
make test
make coverage
```

## Hard stops

- Do not add a build step, bundler, CSS framework, or CDN dependency.
- Do not add `capture="environment"` to the photo input.
- Do not change the Atkinson font family, size, or line height on form controls without an explicit
  accessibility decision — it is a recorded product decision.
- Do not cache HTML or authenticated responses in the service worker; it is static-assets-only by
  design.
- Do not remove a CSRF token, focus indicator, skip link, or ARIA attribute to simplify markup.
