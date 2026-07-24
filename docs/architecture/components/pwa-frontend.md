# Component Design: PWA Shell & Frontend

Modules: `bourbonbook/templates/`, `bourbonbook/static/`
Related: [HLDD](../hldd.md) · [Bottle, shopping-list & sharing workflow](bottle-workflow.md)

## Responsibility

Render every user-facing page server-side, present an installable app shell on iPhone and desktop,
and provide the small amount of client-side interactivity that a server-rendered app still needs
(image preview, a confirm dialog, copy-to-clipboard).

## App assembly

`main.create_app()` mounts `/static` (`bourbonbook/static/`) and `/images` (a sibling top-level
`images/` directory used for a couple of static marketing/reference images), and configures
`Jinja2Templates(directory=ROOT / "templates")` with one custom filter (`money`) registered on the
environment. There is no client-side router or SPA framework — every navigation is a normal page
load or a form POST followed by a `303` redirect.

## Template inventory

`base.html` (shared layout, PWA meta tags), `_mobile_nav.html` / `_collection_header.html` /
`_compact_grid.html` (shared partials), plus one template per page: `library.html`, `compact.html`,
`detail.html`, `new.html`, `edit.html`, `shopping_list.html`, `shared_collection.html`,
`profile.html`, `login.html`, `register`-adjacent flows (`check_email.html`, `verify_email.html`,
`forgot_password.html`, `reset_password.html`, `account_deleted.html`), and an `admin/` subtree
(`users.html`, `user_detail.html`, `catalog.html`, `catalog_import.html`, `config.html`,
`usage.html`). Email bodies live separately under `templates/email/` (see
[Observability & operations](observability-and-operations.md)).

## PWA shell

- **Manifest** (`static/manifest.webmanifest`, also served via an explicit `GET
  /manifest.webmanifest` route with `media_type="application/manifest+json"`): `display:
  standalone`, dark theme/background (`#0d0c0b`), a single scalable SVG icon
  (`sizes: any, purpose: any maskable`) — no raster icon set, no `shortcuts` array.
- **Service worker** (`static/sw.js`, cache name `bourbon-book-v4`): precaches a fixed shell
  (`app.css`, `app.js`, `icon.svg`, the manifest) on install, purges old-versioned caches on
  activate, and on fetch only intercepts same-origin GET requests under `/static/` or the manifest
  path — cache-first with network fallback. **HTML pages, API calls, and photo/avatar media are
  explicitly excluded**, so the app has offline availability of its static shell only, not an
  offline data experience. Registered unconditionally on window `load`
  (`navigator.serviceWorker.register('/static/sw.js')`) with no update-prompt or
  `beforeinstallprompt` capture.
- **Install affordances**: `base.html` sets `apple-mobile-web-app-capable`,
  `apple-mobile-web-app-status-bar-style`, `theme-color`, and `apple-touch-icon` meta tags for iOS
  "Add to Home Screen"; there is no custom in-app install banner/button.
- **Accessible typography**: self-hosted Atkinson Hyperlegible Next (`static/fonts/*.woff2`, with an
  `OFL.txt` license) applied to form values, search/sort controls, and price/quantity fields at
  `1.05rem`/`1.45` line-height — button typography is deliberately left unchanged. This was a
  deliberate accessibility improvement (`docs/adr/plan.md` action A02), not a default framework
  style.

## Client-side JavaScript (`static/app.js`)

A small, dependency-free script handling:

- Live avatar/photo preview via `URL.createObjectURL` before upload.
- The "became Empty" bottle-edit confirm `<dialog>` (remove / add to shopping list / cancel).
- Collection-share confirm-before-replace guard and a copy-to-clipboard button for the generated
  share URL.
- Service worker registration.

No build step, bundler, or framework is involved — the file is served as-is from `/static/`.

## Design properties worth preserving

- The service worker's narrow fetch-interception scope (static assets only) is intentional: it
  avoids the class of bugs where a stale cached HTML page or stale cached bottle photo is served
  after a user's own edit. Broadening it to cache HTML or media should be a deliberate, tested
  decision, not an incidental change.
- Because there's no SPA state layer, every piece of "current state" the UI shows (flash messages,
  the one-time share URL, `?analysis=complete` banners) is threaded through either the Jinja render
  context, a one-shot session pop, or a redirect query string — a new feature needing to show
  post-redirect state should follow one of those three existing patterns rather than introducing a
  fourth mechanism.
