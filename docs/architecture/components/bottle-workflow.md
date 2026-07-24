# Component Design: Bottle, Shopping-List & Sharing Workflow

Modules: `bourbonbook/main.py` (bottle/shopping-list/sharing/avatar/collection routes),
`bourbonbook/photos.py`
Related: [HLDD](../hldd.md) · [AI analysis](ai-analysis.md) · [Pricing & catalog](pricing-and-catalog.md)

## Responsibility

Everything a signed-in user does with their own collection: add/edit/delete bottles, upload and
serve photos and avatars, maintain a shopping list, and optionally publish a read-only public link to
their collection. All routes here require `auth.require_verified_user()` except the public
`/shared/...` pair.

## Route inventory

| Route | Purpose |
| --- | --- |
| `GET /`, `GET /collection/compact` | Main and compact library views, with search (`q`) and `sort`; excludes shopping-list/empty items |
| `GET /bottles/new`, `POST /bottles` | New-bottle form and creation (photo → analysis → catalog enrichment → pricing) |
| `GET /bottles/{id}` | Detail view |
| `GET /bottles/{id}/edit`, `POST /bottles/{id}/edit` | Edit form and save, including the "became Empty" transition |
| `POST /bottles/{id}/analyze` | Re-run analysis: `photo`, `name`, or `price` mode |
| `POST /bottles/{id}/delete` | Delete bottle + photo file |
| `GET /media/{photo_name}` | Serve a bottle photo, ownership-checked |
| `GET /shopping-list`, `POST /shopping-list` | List and add shopping-list items (camera or file picker) |
| `POST /shopping-list/{id}/photo` | Attach/replace a photo on a shopping item |
| `POST /shopping-list/{id}/purchased` | Convert a shopping item into a normal collection bottle |
| `POST /shopping-list/{id}/delete` | Delete a shopping-list item |
| `POST /collection/share`, `POST /collection/share/disable` | Generate/revoke a public share token |
| `GET /shared/{token}`, `GET /shared/{token}/media/{photo_name}` | Public, unauthenticated read-only view |
| `POST /profile/avatar`, `POST /profile/avatar/remove` | Upload/remove the user's avatar |
| `GET /avatars/{avatar_name}` | Serve the current user's own avatar |

## Photo pipeline (`photos.py`)

`save_photo(upload, upload_dir, max_mb)`:

1. Reads at most `max_mb*1024*1024 + 1` bytes, rejecting (`413`) anything over the configured
   `MAX_UPLOAD_MB` without buffering the whole file first.
2. `validated_image()`: Pillow `.verify()` (catches truncated/corrupt files, requiring a fresh
   re-open afterward per Pillow's API), `ImageOps.exif_transpose()` (auto-rotate), convert to `RGB`
   (normalizes away alpha/CMYK/palette modes). Catches `Image.DecompressionBombError` explicitly —
   this doubles as a DoS mitigation against maliciously crafted images.
3. `image.thumbnail((1800, 1800), LANCZOS)` — downsizes in place, aspect-preserving, never upscales.
4. Stores as `f"{uuid.uuid4().hex}.jpg"` (collision-proof, not derived from user input) under the
   caller-supplied directory, JPEG quality 88, `optimize=True`.

`save_avatar(upload, avatar_dir)` reuses the same validation pipeline with a stricter 10 MB limit and
`ImageOps.fit(image, (512, 512), LANCZOS)` (center-crop-to-fill square) instead of `thumbnail`, JPEG
quality 85.

## Shopping list model

Not a separate table — a shopping-list item **is** a `Bottle` row with `status="Empty"` and/or
`on_shopping_list=True` (either flag independently qualifies it, `is_shopping_item()`). This means:

- The main collection query (`collection_statement`) explicitly excludes both conditions, so
  shopping items never leak into the primary library view.
- An existing bottle edited down to `status="Empty"` can be converted to a shopping-list entry
  through a client-side confirm `<dialog>` (`static/app.js`), or removed outright, or blocked from
  saving until the user picks one of those choices.
- "Found it" (`POST /shopping-list/{id}/purchased`) reverses the transition: clears
  `on_shopping_list`, sets `status="Unopened"`, `fill_level=100`.

## Collection sharing

A single opaque per-user token, generated with `secrets.token_urlsafe(32)`. Only its digest
(`tokens.token_digest`, SHA-256-family) is ever persisted, in `User.collection_share_token_hash` —
the raw token exists only in the one-time, session-flashed confirmation URL shown right after
generation. `shared_collection_user()` resolves the public route by digest lookup (rejecting
overlong tokens up front). The public routes reuse the same `collection_statement` exclusions (no
empty/shopping-list bottles) and add `protect_shared_response()` headers (`Cache-Control: private,
no-store`, `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow`) so a shared link isn't
cached or indexed. Disabling the share simply nulls the hash/timestamp, immediately invalidating any
outstanding link — there is no grace period.

## Design properties worth preserving

- Bottle creation never fails because of an AI provider outage — the photo and row are always
  persisted; only `analysis_status` reflects what happened. See
  [AI analysis](ai-analysis.md).
- The PRG (Post/Redirect/Get) pattern is used consistently: every mutating POST ends in a `303`
  redirect, with transient state passed via query-string flags (`?new=1`, `?analysis=complete`) or a
  one-shot session pop (`new_collection_share_url`) rather than a generic flash-message framework.
- Ownership checks are done per-route (`owned_bottle` helper) rather than via a shared dependency —
  a new route touching `Bottle` rows must remember to scope the query to the current user (or, for
  the `/shared/...` routes, to the resolved share-token owner).
