---
name: c4-diagram-update
description: "Updates Bourbon Book's as-built architecture documentation — the HLDD, the C1-C4 views, the per-component design docs, and the rendered Mermaid SVGs under docs/architecture/diagrams/. Covers which view owns what, how to regenerate an SVG so it does not drift from its Mermaid source, and the as-built rule that these docs describe only merged code. Use in the post-merge documentation pass or when invoking $c4-diagram-update."
---

## Context

```
docs/architecture/
  hldd.md                      # behavior: request flow, subsystems, data model, cross-cutting
  c1-system-context.md         # people and external systems
  c2-containers.md             # the container, its volume, its neighbors
  c3-components.md             # modules inside the app container  ← changes most often
  c4-code.md                   # key classes and call paths
  components/                  # one design doc per component + README index
  diagrams/*.svg               # rendered output, committed
```

Each C-view `.md` holds **exactly one** ```mermaid fenced block, plus a header linking to its
rendered SVG and its governing ADRs. The SVGs are committed so they render in contexts that do not
execute Mermaid.

**The as-built invariant:** every file here describes only what is checked into the repository
today. Proposed work lives in `docs/adr/plan.md` and in `Proposed` ADRs. Never document a design
that has not merged.

## The drift hazard — read this first

There is **no render target in the Makefile and no CI check** that the SVGs match their Mermaid
sources. They drift silently, and they already have: as of this writing the four SVGs were last
committed on 2026-07-05 while the Mermaid sources were edited on 2026-07-22. An SVG that disagrees
with its source is worse than no SVG, because it is confidently wrong.

So: **if you change a Mermaid block, you regenerate its SVG in the same change.** If you cannot
regenerate it, say so explicitly in your report rather than leaving a stale file in place.

## Regenerating a diagram

```bash
npx -y @mermaid-js/mermaid-cli \
  -i docs/architecture/c3-components.md \
  -o docs/architecture/diagrams/c3-components.svg \
  -b transparent
```

Notes:

- Pointing `-i` at the `.md` extracts its fenced Mermaid block. Because each view holds exactly one
  block, the output lands at the given path. If you ever add a second block to a view, `mmdc` will
  emit numbered files — don't; keep one diagram per view.
- `-b transparent` matches the existing committed SVGs.
- Verify the result renders and is not empty (a syntax error can produce a near-empty SVG), then
  confirm the `viewBox` looks sane rather than assuming success.
- Diff the SVG before committing. A large diff for a one-node change usually means a version bump in
  `mermaid-cli` reflowed the whole layout — that is fine, but say so, because the reviewer will
  otherwise wonder why the diagram exploded.

A `make diagrams` target that renders all four and a `pr-check` assertion that the SVGs are current
would make this deterministic instead of a discipline problem. That is a code change, so it belongs
to `senior-engineer` as a roadmap action rather than being slipped in here.

## Which document owns what

Update only what the change actually touched:

| Change | Update |
|---|---|
| New or removed module in `bourbonbook/` | `c3-components.md` + SVG, the matching `components/*.md`, HLDD §4 |
| New class, call path, or key sequence | `c4-code.md` + SVG, the component doc's internals section |
| New external system or provider | `c1-system-context.md` + SVG, `c2-containers.md` if deployment changed |
| New volume, port, health check, env contract | `c2-containers.md` + SVG, HLDD §7 |
| Behavior change inside an existing component | the component doc only — often no diagram change |
| New component doc | `components/README.md` index table, HLDD's detailed-design link |

Most changes need **fewer** documents than they first appear to. C3 regenerated for a behavior
change that added no module is churn. Prefer the narrowest true update.

## Keeping it honest

- **Verify counts and claims against the code.** The HLDD currently says `main.py` is ~2,080 lines;
  it is ~2,780. Numbers in prose rot — either check them when you touch the section or replace them
  with something durable.
- **Cross-links must survive.** Each C-view header cites its governing ADRs; `components/README.md`
  indexes every component doc; the HLDD metadata block lists the ADRs and links the C-views. Adding
  a document means adding it to the index.
- **Mermaid style**: match the existing `flowchart LR` with `subgraph` grouping in C3. Keep node IDs
  stable across edits so diffs stay readable — renaming an ID rewrites every edge that references
  it.
- **Do not describe the roadmap here.** The HLDD explicitly excludes the Phase 2 pricing/RAG work;
  keep it that way.

## Verify

```bash
grep -c '^```mermaid' docs/architecture/c*.md    # expect 1 per view
ls -la docs/architecture/diagrams/                # SVGs newer than their sources
git status --short docs/architecture/
```

Confirm every regenerated SVG is staged alongside the `.md` it came from. A commit that changes a
Mermaid source without its SVG reintroduces the drift this skill exists to prevent.

## Hard stops

- Never document unmerged work in `docs/architecture/`.
- Never edit a Mermaid block without regenerating its SVG in the same change, or explicitly
  reporting that you could not.
- Never add a second Mermaid block to a C-view.
- Never delete a component doc that still has an index entry, or add one without indexing it.
