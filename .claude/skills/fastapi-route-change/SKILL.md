---
name: fastapi-route-change
description: "Workflow for adding or changing a Bourbon Book HTTP route or handler in bourbonbook/main.py — authentication, CSRF, owner scoping, the admin boundary, form parsing, redirects and status codes, rate limiting, session handling, and the async job pattern. Use whenever a change touches a route decorator, a request handler, or the helpers they share. Complements pwa-visual-check, which covers the templates those routes render."
---

## Context

`bourbonbook/main.py` is ~2,780 lines and holds essentially all routing and orchestration. Routes
are registered inside `register_routes(app)` as nested functions, not as a `APIRouter` per module.
Follow that structure; do not introduce a router package as a side effect of an unrelated change.

**The shared helpers you must use rather than reinvent:**

| Helper | Where | Purpose |
|---|---|---|
| `current_user(request, session)` | `auth.py` | Optional user; returns `None` when anonymous |
| `require_user(request, session)` | `auth.py` | Authenticated user or redirect |
| `require_verified_user(request, session)` | `auth.py` | **The default for user-facing routes** — verified email required |
| `require_admin(request, session)` | `auth.py` | Admin boundary |
| `csrf_token(request)` | `auth.py` | Reads/creates the session token |
| `verify_csrf(request, token)` | `auth.py` | **Every state-changing POST** |
| `render(request, name, **ctx)` | `main.py` | Template response; injects `csrf_token` automatically, accepts `status_code` |
| `owned_bottle(session, user, id)` | `main.py` | Ownership-scoped fetch — `None` when not the owner |
| `collection_statement(user, q, sort)` | `main.py` | Owner-scoped collection query |
| `parse_float` / `parse_int` | `main.py` | Tolerant form-value coercion with bounds |
| `limited(request, op, email)` / `too_many(request, mode)` | `main.py` | Rate limiting on auth operations |

## The canonical shapes

**Authenticated GET:**

```python
@app.get("/bottles/{bottle_id}", response_class=HTMLResponse)
def bottle_detail(request: Request, bottle_id: int) -> Response:
    with app.state.database.session_factory() as session:
        user = require_verified_user(request, session)
        bottle = owned_bottle(session, user, bottle_id)
        if not bottle:
            return RedirectResponse("/", 303)
        return render(request, "detail.html", user=user, bottle=bottle)
```

**State-changing POST** — CSRF is verified **before** the session opens, from the parsed form:

```python
@app.post("/bottles/{bottle_id}/edit")
async def save_bottle(request: Request, bottle_id: int) -> Response:
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    with app.state.database.session_factory() as session:
        user = require_verified_user(request, session)
        bottle = owned_bottle(session, user, bottle_id)
        if not bottle:
            return RedirectResponse("/", 303)
        ...
    return RedirectResponse(f"/bottles/{bottle_id}", 303)
```

Either `await request.form()` or `Annotated[str, Form(alias="csrf_token")]` is fine — `POST
/bottles` uses the latter. Be consistent within a handler.

**Long-running work** — commit first, then hand off to a background task in the same process, and
let the client poll:

```python
@app.post("/bottles", status_code=202)
...
    session.commit()
    bottle_id = bottle.id          # read IDs before the session closes
background_tasks.add_task(run_add_bottle_pipeline, session_factory, bottle_id, ...)
return JSONResponse({"bottle_id": bottle_id}, status_code=202)
```

The paired `GET /bottles/{id}/status` route is what the page polls. Never block a request on a model
call.

## Rules

1. **Pick the right guard.** `require_verified_user` is the default for user-facing routes;
   `require_admin` for anything under `/admin`. Only use `current_user` where anonymous access is
   genuinely intended (shared collections, auth pages). Getting this wrong is the highest-severity
   mistake in this file.
2. **Owner scoping is not optional.** Never fetch a `Bottle` by ID alone. Use `owned_bottle` or an
   equivalent `WHERE owner_id == user.id`. A missing or non-owned row redirects to `/` with `303` —
   it does not 404 or 403, because that distinction leaks existence.
3. **Every state-changing POST verifies CSRF**, including deletes, toggles, and admin actions.
4. **Sessions are scoped with `with ... as session:`.** Read any attribute you need after the block
   (`bottle.id`, `user.id`) into a local **before** it closes. Detached-instance bugs are the most
   common defect here.
5. **`POST` → `303` redirect**, never render directly, so refresh doesn't resubmit. Rate-limited auth
   responses render with `429`.
6. **Rate-limit new auth-adjacent operations** (login, register, resend, reset) via `limited()` and
   `too_many()`. Anything that emails a user or checks a credential qualifies.
7. **Coerce form values with `parse_float` / `parse_int`**, with explicit bounds. Never trust a raw
   form string into the model.
8. **Uploads** go through `save_photo` with `max_upload_mb` enforced; never write a client-supplied
   filename to disk.
9. **New configuration** means updating `Settings`, `.env.example`, and `/admin/config` validation
   together — and secrets are never rendered back.
10. **Record usage and metrics** for any new AI/provider operation, with nothing sensitive in the
    payload.

## Tests

Route tests use the in-process FastAPI `TestClient` (see `tests/test_app.py`,
`tests/test_admin.py`, `tests/test_profile.py`). For every new or changed route assert:

- the happy path and the rendered markup you care about;
- **anonymous access** redirects and leaks nothing;
- **unverified user** behavior where the route requires verification;
- **another user's ID** returns the redirect, not their data;
- **admin routes** reject a non-admin;
- **missing or wrong CSRF token** is rejected;
- invalid form values are handled rather than raising;
- rate-limited operations return `429` after the threshold.

```bash
make lint
make test
make coverage
make security
```

## Hard stops

- Never add an authenticated route without a guard, CSRF on writes, and owner scoping.
- Never fetch a user-owned row by primary key alone.
- Never do provider, network, or model work inline in a request handler — queue it and poll.
- Never introduce a second web framework, an `APIRouter` restructure, or a dependency-injection
  layer as a side effect of a feature change. That is an architecture decision and needs an ADR.
- Never return a different status or message for "not found" versus "not yours".
