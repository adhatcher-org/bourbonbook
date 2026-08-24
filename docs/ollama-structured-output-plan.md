# Ollama Structured Output — Implementation Plan

**Action:** A15 — Constrain Ollama bottle-analysis output
**Status:** Planned; unstarted. `OLLAMA_STRUCTURED_OUTPUT` appears nowhere in `bourbonbook/`,
`tests/`, `.env.example`, or `README.md`.
**Verified against:** `origin/main` at `419d248` (merge of PR #68, A14), 2026-08-21.
**Revision:** round-2, 2026-08-22 — revised after senior engineering review. Changes from round-1 are
summarised in "Round-2 revisions" at the end of this file.
**Authority:** this file. `docs/adr/plan.md` carries the action tracker row; the Obsidian note
`Planning/Ollama Structured Output Implementation Plan.md` is a navigable summary of this file.
**Source review:** the Obsidian note `Planning/Ollama Photo Extraction Review.md` (vault
`01 Projects/Bourbon Book`), Finding 2. There is no `docs/ollama-vision-review.md` in this
repository; the review has never been landed here, and an engineer executing A15 must read the
vault note.
**Dependencies:** none outstanding. A14 / PR #68 is merged at `419d248`; every contract this plan
builds on is present on `origin/main` and is restated with line numbers in "Verified starting
point" below.

## Delivery intent

Replace the local bottle-analysis request's unconstrained `"format": "json"` with an explicit JSON
Schema, behind a managed operator switch that **ships disabled**. Today the OpenAI path is
schema-constrained (`bourbonbook/openai_provider.py:28-48`, a Pydantic `BottleAnalysis` passed as
`text_format`) and the Ollama path is not, so the two providers are not compared on equal terms.

This plan does **not** assert what a given Ollama build accepts in `format`. Structured output on
`/api/generate` is a version-gated Ollama feature, this repository pins no Ollama version
(`README.md:124` and `.env.example:6` point `OLLAMA_URL` at an arbitrary operator-run host, and no
minimum version is documented anywhere), and the schema this plan specifies uses two constructs —
a union `"type": ["string","null"]` carrying an `enum`, and a bare `{"type": "null"}` on a
required property — whose survival through llama.cpp's schema→grammar conversion is unverified.
**Step 0 below is a compatibility probe against the operator's own endpoint, and it runs before any
production code is written.** Its outcome selects the schema representation the rest of the plan
uses. Nothing downstream depends on an unverified claim about the runtime.

This is a provider-contract change plus two narrowly-scoped correctness fixes it forces (the
empty-string persistence guard in §5 and the `thinking`-channel guard in §6). It does not alter
prompts, image processing, the add-bottle pipeline, database schema, catalog extraction, model
selection, or provider routing. It is explicitly **not** a benchmark or model-selection gate: ADR
0003 (`docs/adr/0003-fixed-local-model-no-benchmark-gate.md`, Decision 3) retired that gate, and
every deterministic test must stay offline.

## Verified starting point (post-A14, `419d248`)

Read this section before editing. Each claim was checked against the merged tree.

### The request payload A15 modifies

`bourbonbook/ollama.py::request_analysis` spans lines 115-210. The payload literal is
`bourbonbook/ollama.py:121-128`:

```python
output_fields = PHOTO_OUTPUT_FIELDS if photo else OUTPUT_FIELDS  # ollama.py:119
field_list = ", ".join(output_fields)  # ollama.py:120
payload: dict[str, Any] = {
    "model": model,  # ollama.py:122
    "prompt": f"{prompt}\nReturn ONLY one JSON object with these keys: {field_list}.",  # :123
    "stream": False,  # ollama.py:124
    "think": False,  # ollama.py:125
    "format": "json",  # ollama.py:126  <- A15
    "options": {"temperature": 0.1, "num_ctx": analysis_context_window(settings, photo)},  # :127
}
if photo:
    payload["images"] = [base64.b64encode(photo.read_bytes()).decode("ascii")]  # :129-130
```

Facts that constrain the edit:

- `options` holds **exactly two** keys: `temperature: 0.1` and `num_ctx`. There is **no** `seed`
  and **no** `num_predict` anywhere in `bourbonbook/` or `tests/`. The temperature to preserve in
  **both** switch states is `0.1` (see §3 — A15 does not change temperature).
- `num_ctx` is **not** a literal. `analysis_context_window(settings, photo)`
  (`bourbonbook/ollama.py:41-43`) delegates to `Settings.ollama_context_window(vision=...)`
  (`bourbonbook/config.py:228-232`). Rewriting the `options` dict wholesale would regress the A14
  per-role context-window contract and break `tests/test_ollama.py:63` and
  `tests/test_ollama.py:122`. Keep the `num_ctx` expression byte-identical.
- The POST target is `f"{settings.ollama_url}/api/generate"` (`bourbonbook/ollama.py:147`).
- The response filter drops **only `None`**: `values = {key: parsed.get(key) for key in
  output_fields if parsed.get(key) is not None}` (`bourbonbook/ollama.py:155`). It does **not**
  drop `""`. This matters — see §5.
- The parsed body is `raw_output = body.get("response") or body.get("thinking")`
  (`bourbonbook/ollama.py:153`). The `thinking` fallback is a real, test-codified shape
  (`tests/test_ollama.py:44`, `test_qwen_thinking_field_is_accepted`, whose fake returns
  `{"response": "", "thinking": '{"name":...}'}`). This matters — see §6.
- The HTTP client timeout is a hard `httpx.AsyncClient(timeout=120)`
  (`bourbonbook/provider_clients.py:50`). There is no `num_predict` ceiling on generation length.

### Adjacent payloads that must not change

- `warm_vision_model` — `bourbonbook/ollama.py:234-240`: `{"model": model, "options": {"num_ctx":
  settings.ollama_context_window(vision=True)}}`. No prompt, no `format`.
- `bourbonbook/catalog_extract.py:225-228`: `"format": "json"` with `{"temperature": 0, "num_ctx":
  settings.ollama_context_window(vision=True)}`. Catalog extraction has a distinct array contract
  and is out of scope.
- `bourbonbook/ollama_search.py:168`: `/api/chat` with `{"num_ctx":
  settings.ollama_context_window(vision=False)}`.

### The field tuples the schema derives from

`bourbonbook/analysis.py:19-41`:

- `FIELDS` (`:19-38`) — 18 names: `name`, `brand`, `release`, `edition`, `spirit_type`,
  `distilled_by`, `mash_bill`, `proof`, `abv`, `size`, `age_statement`, `barrel_number`,
  `bottle_number`, `warehouse`, `floor`, `status`, `fill_level`, `msrp`.
- `OUTPUT_FIELDS = FIELDS + ("ocr_text",)` (`:39`) — 19 fields, used for name analysis.
- `PHOTO_OUTPUT_FIELDS = OUTPUT_FIELDS + ("date_bottled",)` (`:40`) — 20 fields, photo only,
  `date_bottled` last.
- `PHOTO_BOTTLED_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")` (`:41`).

Membership derivation is therefore viable and must be **per path**, not one shared schema. Schema
property **membership** equals the tuple; schema property **order** does not — see §1.

### Contracts the schema must not disturb

- `date_bottled` is carried out of band. `analyze_bottle` (`bourbonbook/analysis.py:294-308`) parses
  it at `:296` via `normalize_photo_bottled_date` (`:258-265`), strips it from `values` at `:297`,
  and pops it again after text refinement at `:307`. It is returned on the frozen
  `PhotoAnalysisResult` dataclass (`bourbonbook/analysis.py:94-100`), never inside `values`.
- The AI can never write a lifecycle date through the merge path:
  `ANALYSIS_EXCLUDED_FIELDS = set(LIFECYCLE_DATE_FIELDS)` (`bourbonbook/main.py:464`) and the skip
  at `bourbonbook/main.py:468-470`. The one AI write path is `apply_photo_bottled_date`
  (`bourbonbook/main.py:483-486`), fill-if-empty only.
- `msrp` is suppressed on the ordinary merge path: `merge_analysis` skips it unless
  `allow_msrp=True` (`bourbonbook/analysis.py:136-147`), which only `enrich_from_verified_catalog`
  (`bourbonbook/analysis.py:150-159`) sets. A null-only `msrp` in the schema is therefore a
  no-behaviour-change tightening on the model's own output. It is **not** a guard on the catalog
  path — see §1's `ocr_text` rule and §7.
- The status vocabulary is `Unopened` / `Opened` / `Empty`, derived in `normalize_analysis`
  (`bourbonbook/analysis.py:237-255`) and mirrored in `openai_provider.BottleAnalysis.status`
  (`bourbonbook/openai_provider.py:44`). Note that `normalize_analysis` assigns a status **only
  when `fill_level` parses as a number** — it returns early at `analysis.py:242-243` otherwise —
  so today a raw model string such as `"sealed"` passes straight through to `apply_analysis` and
  into `bottle.status`. Constraining the enum closes that pass-through; that is a deliberate
  behaviour change, documented in §1.

### The configuration registry, which is test-enforced

`tests/test_config_registry.py` (unchanged by PR #68) makes adding a setting a **three-edit**
operation with no escape hatch:

1. a `Settings` field in `bourbonbook/config.py` plus its `from_env` parse, **and**
2. a `ConfigField` in `admin_config.CONFIG_FIELDS` **or** an `ENV_ONLY_SETTINGS` entry carrying a
   non-empty reason, **and**
3. an `.env.example` line matching `^([A-Z0-9_]+)=` (`tests/test_config_registry.py:39` —
   `re.finditer(r"(?m)^([A-Z0-9_]+)=", text)`; a line beginning with `#` does not match).

Enforced by `test_every_setting_is_registered_or_explicitly_env_only`
(`tests/test_config_registry.py:42`), `test_env_only_allowlist_has_no_stale_entries` (`:54`),
`test_every_config_field_maps_to_a_real_settings_attribute` (`:73`),
`test_every_registered_field_is_documented_in_env_example` (`:81`), and
`test_env_example_documents_no_unknown_keys` (`:92`). Do not edit these tests.

`ENV_ONLY_SETTINGS` (`bourbonbook/admin_config.py:285-329`) is not the right home for this setting:
it is a rollout control an operator must be able to flip and roll back, so it gets a `ConfigField`.

### How the switch is actually flipped — read this before writing the rollback story

`Settings.from_env` builds `values: Mapping[str, str] = {**os.environ, **overrides}`
(`bourbonbook/config.py:90`), where `overrides = load_managed_overrides()` reads `DATA_DIR/.env`.
**Managed values outrank the environment.** Once an admin saves configuration even once,
`write_managed_config` (`bourbonbook/admin_config.py:495-504`) writes every `CONFIG_FIELDS` key
present in `persisted`, and each line is serialised as
`f"{field.key}={json.dumps(values[field.key])}"` (`:501`) — so the byte on disk is
`OLLAMA_STRUCTURED_OUTPUT="false"`, **with literal double quotes**, which `_decode_value`
(`admin_config.py:369-374`) strips on read.

Three consequences bind this plan:

- An `OLLAMA_STRUCTURED_OUTPUT` environment variable **cannot** override a managed key. Any
  procedure that says "export the variable and restart" is a no-op on a system whose admin UI has
  been used. The switch is flipped in the admin config UI, or by editing `DATA_DIR/.env`
  (`data/.env` in the local layout) directly.
- The deployment feeds the container that same file: `--env-file
  .../data/.env` (`run-docker.sh:15`) and `env_file: .env` (`compose.yaml:11-13`). So the *raw*
  `os.environ` value the process sees can be the JSON-quoted `"true"`. Any strict parser must
  tolerate surrounding double quotes — see §2.
- `make benchmark-run` runs `uv run --env-file data/.env` (`Makefile:73`) — the same file — so a
  shell-exported flip is doubly ignored there. See "Optional post-merge accuracy diagnostic".

Boolean `ConfigField`s are strictly parsed in the admin surface: `_parse_field`
(`bourbonbook/admin_config.py:550-558`) rejects anything but the exact lowercase strings `true` /
`false`, with no trim and no case-fold. The environment parse is the lenient half —
`Settings.from_env` uses `get("SECURE_COOKIES", "false").lower() == "true"`
(`bourbonbook/config.py:97`), which silently treats a typo as `false`. §2 states exactly where A15
sits between those two and why.

### Repository docs that do not yet mention A15

`docs/adr/plan.md` has no A15 row; the tracker table's last row is A14 at `docs/adr/plan.md:98`.
`README.md:126-130` and `README.md:176-178` document the Ollama context-window settings and are the
right neighbours for the new one.

## Design

### 0. Step 0 — endpoint compatibility probe (first work item, before any production code)

**Nothing in §1-§7 may be written until Step 0 has produced a recorded result.** This is a
throwaway script, not a repository artefact, run by the operator against their own configured
`OLLAMA_URL` with the configured vision model, with explicit approval.

Probe, in this order, each as one `POST /api/generate` with `"stream": false`, a one-line prompt,
and no image:

| # | `format` value | What it proves |
|---|---|---|
| P0 | `"json"` | the endpoint and model are reachable; baseline |
| P1 | a minimal object schema, one property `{"type": ["string","null"]}`, `required`, `additionalProperties: false` | schema objects are accepted at all (the Ollama version floor) |
| P2 | P1 plus a second property `{"type": ["string","null"], "enum": ["Unopened","Opened","Empty", null]}` | union type **and** `enum` on one node survives grammar conversion |
| P3 | P1 plus a required property `{"type": "null"}` | a null-only required property is accepted |
| P4 | the full `analysis_schema(photo=True)` draft from §1 | the real payload, end to end |

Record only: probe id, pass/fail, HTTP status, the Ollama version string from `GET /api/version`,
the model identifier, and wall-clock duration. Record **no** response body, prompt, or error text.

The result selects the representation §1 implements:

- **P1-P4 all pass** → implement §1 exactly as written. This is the intended path.
- **P2 fails** (union type plus enum rejected) → **fallback representation R1**: drop the union on
  `status` and express it as `{"enum": ["Unopened", "Opened", "Empty", None]}` with no `"type"`
  key. Nullability is still expressed, by the `None` member. Update the §1 table and the type
  test accordingly.
- **P3 fails** (`{"type": "null"}` rejected on a required property) → **fallback representation
  R2**: express `msrp`, and `ocr_text` on the name path, as `{"type": ["string","null"], "const":
  None}`; if `const` also fails, as `{"type": ["string","null"]}` plus an explicit post-parse drop
  of the field in `request_analysis` for the paths where it must be null. Prefer the schema-level
  form; the post-parse drop is the last resort and must be covered by its own test.
- **P1 fails** (no schema support on this endpoint) → **A15 does not ship the switch enabled on
  this deployment at all.** Land the code with the default disabled (which is what §2 specifies
  regardless), and record the endpoint's Ollama version as the blocking prerequisite in
  `docs/adr/plan.md`.

Write the outcome, including the observed Ollama version, into the A15 section of
`docs/adr/plan.md` as the stated minimum-version prerequisite. That version string is the only
compatibility claim this project makes; do not generalise it.

Step 0 is a **required pre-deploy step**, not an optional post-merge one. It is not an *acceptance
criterion* in the sense of ADR 0003 — no accuracy number is being gated — but no engineer may open
the A15 PR without its recorded result in the PR description.

### 1. One explicit schema, derived from the output tuples

Add to `bourbonbook/analysis.py`, next to the field tuples:

- `ANALYSIS_STATUS_VALUES = ("Unopened", "Opened", "Empty")`.
- `ANALYSIS_FIELD_SPECS`: a module-level mapping from every name in `PHOTO_OUTPUT_FIELDS` to its
  JSON Schema property specification.
- `ANALYSIS_SCHEMA_FIELD_ORDER`: the property emission order, defined as
  `("ocr_text",) + tuple(f for f in PHOTO_OUTPUT_FIELDS if f != "ocr_text")`. **Order is derived
  from the tuples but is deliberately not tuple order** — see the ordering rationale below.
- `analysis_schema(*, photo: bool) -> dict[str, Any]`: selects `PHOTO_OUTPUT_FIELDS` when `photo`
  is true, `OUTPUT_FIELDS` otherwise, and returns a schema object whose properties are the
  selected tuple's members emitted in `ANALYSIS_SCHEMA_FIELD_ORDER`.

Specification rules:

| Fields | Specification |
|---|---|
| every textual field, `date_bottled` | `{"type": ["string", "null"]}` |
| `ocr_text` — **photo path only** | `{"type": ["string", "null"]}` |
| `ocr_text` — **name path only** | `{"type": "null"}` |
| `proof`, `abv` | `{"type": ["number", "null"]}` |
| `fill_level` | `{"type": ["integer", "null"]}` |
| `status` | `{"type": ["string", "null"], "enum": ["Unopened", "Opened", "Empty", None]}` |
| `msrp` | `{"type": "null"}` |

`ocr_text` is the only field whose specification differs between the two paths, so
`analysis_schema` takes its per-field spec from `ANALYSIS_FIELD_SPECS` and then applies exactly one
path override: on the name path, `ocr_text` becomes `{"type": "null"}`. The rationale is in §7 —
a name-only call has no image, so a non-null `ocr_text` from it is by construction a
hallucination, and a hallucinated OCR string is a live path to a false catalog match that writes
an MSRP. Keeping the property present (rather than deleting it) preserves the 19/20 property
counts, keeps schema membership equal to the output tuple, and keeps the schema aligned with the
unchanged prompt key list at `bourbonbook/ollama.py:123`.

Schema object rules:

- `"type": "object"`.
- `properties` inserted in `ANALYSIS_SCHEMA_FIELD_ORDER`, restricted to the selected tuple's
  members. For the photo path that is `name … msrp`, then `date_bottled`, then `ocr_text`
  **last** (see Round-4 revision R4-2). For the name path, `name … msrp`, then `ocr_text` last.
- `"required"` lists every property, in the same order. Nullability, not optionality, expresses
  "unknown".
- `"additionalProperties": false`.
- No `pattern`, `minimum`, `maximum`, `format`, or business range. The schema constrains **shape**,
  not truth.

**Ordering rationale (do not "fix" this back to tuple order).** llama.cpp's schema→grammar
conversion emits an all-required object's properties as a fixed sequence, and the model cannot emit
EOS until every property is present. Two things follow. First, `ocr_text` is the transcription
scratchpad the `PHOTO_PROMPT` (`bourbonbook/analysis.py:56-91`) asks the model to fill, and today
the model is free to emit it first; with `"think": False` (`bourbonbook/ollama.py:125`) it is the
*only* scratchpad. Ordering it 19th — after every field it is supposed to inform — removes that.
Emitting it first preserves it. Second, generation length becomes "all 20 properties, always"
rather than "as many keys as the model chose". Against a hard 120s client timeout
(`provider_clients.py:50`) and no `num_predict`, a full `ocr_text` transcription plus 19 further
keys is a plausible route to `ReadTimeout` → `error_type="timeout"` → `({}, "unavailable")`. That
risk is why §"Operational verification" carries a mandatory p95 observation with a stated ceiling,
and why the latency check in the diagnostic section is downgraded but **replaced**, not simply
dropped.

**Fail-fast parity lives in one helper, `_validate_analysis_field_specs()`, in
`bourbonbook/analysis.py`, and that helper is called from exactly two places: once at import,
and again as the first statement of `analysis_schema`.** The helper reads
`PHOTO_OUTPUT_FIELDS` and `ANALYSIS_FIELD_SPECS` from module globals (never from arguments) and
raises:

- any name in `PHOTO_OUTPUT_FIELDS` absent from `ANALYSIS_FIELD_SPECS` → `ValueError` naming the
  missing fields;
- any key in `ANALYSIS_FIELD_SPECS` absent from `PHOTO_OUTPUT_FIELDS` (the superset) → `ValueError`
  naming the extra keys.

Both call sites are required, and they do different jobs:

- **The import-time call** is the one that matters in production. It is the correct home for a
  developer error, and it is what answers the two ways a per-call-*only* check would have been
  wrong. A per-call-only check does not run at all when the switch is off (Python does not evaluate
  the untaken branch of `analysis_schema(...) if structured else "json"`, so the claim that drift
  "breaks every analysis request" held only on the enabled path, and operators may run disabled
  indefinitely). And a `ValueError` raised from inside the payload literal is raised **before** the
  `try` at `bourbonbook/ollama.py:145` and is not in the caught tuple at `:178`, so on
  `POST /bottles/{id}/analyze` — `refresh_bottle_analysis`, which has no try/except around
  `analyze_bottle` — it would return an HTTP 500 and discard the user's submitted form.
- **The per-call call** is a guard that, in production, can never fire. Once the module has imported
  successfully the two globals are constant for the life of the process; nothing in A15 rebinds
  them at runtime. The only way the per-call guard raises is when a test monkeypatches a module
  global, which is precisely what
  `test_analysis_schema_fails_fast_on_field_set_drift` does. It therefore reintroduces none of the
  HTTP-500 exposure above: that exposure required drift to survive import, and after the import-time
  call it cannot. `request_analysis` still needs no new exception handling.

This is why the drift test is written against `analysis_schema` and not against module import:
monkeypatching a module attribute after import cannot re-trigger an import-time check, and no test
may reload `bourbonbook.analysis`. Without the per-call call the mandated test is unwritable — an
orphan key in `ANALYSIS_FIELD_SPECS` would simply never be read, and a missing specification would
surface as a bare `KeyError` from `ANALYSIS_FIELD_SPECS[field]` rather than a `ValueError` naming
the field.

`analysis_schema` must resolve `PHOTO_OUTPUT_FIELDS`, `OUTPUT_FIELDS`, `ANALYSIS_FIELD_SPECS`, and
`ANALYSIS_SCHEMA_FIELD_ORDER` from **module globals on every call**. It must not bind them as
default arguments, must not precompute the two schemas at import, and must not be `@lru_cache`d —
`@lru_cache` would additionally violate the isolation rule below, silently. The drift test in
`tests/test_analysis.py` depends on this and is unwritable without it.

Isolation: return `copy.deepcopy` of the assembled tree — or assemble it fresh per call from
deep-copied specifications. Two calls must never share a nested `dict`; mutating one result must not
affect the next request.

The schema does not replace normalization. `normalize_analysis`
(`bourbonbook/analysis.py:237-255`) remains authoritative for proof/ABV reconciliation, size
snapping, and fill-level/status consistency; `normalize_photo_bottled_date` (`:258-265`) remains
authoritative for the exact bottled date; human review remains authoritative for facts.

**Behaviour change on `status`, stated explicitly.** The enum is
`["Unopened","Opened","Empty", None]`. `normalize_analysis` only ever *assigns* those three strings
and never assigns `None`, so the tests compare `set(enum) - {None}` against the assigned
vocabulary, not the raw enum. More substantively: because `normalize_analysis` returns early when
`fill_level` does not parse (`analysis.py:242-243`), today a model answer of `"sealed"` reaches
`apply_analysis` unmodified and is written to `bottle.status`. With the schema enabled the model
cannot emit `"sealed"`; the expected new outcome for that case is `null` — the field is left alone,
and the reviewer sets it. This is a real improvement and a real change, and it is the one item in
"Contracts the schema must not disturb" that A15 does knowingly disturb.

**Keep the existing prompt sentence at `bourbonbook/ollama.py:123` unchanged.** It gives the model
the key list as context, and `tests/test_ollama.py:63`
(`test_photo_and_name_analysis_select_their_configured_models`) asserts on the prompt string —
`"date_bottled" in requests[0]["prompt"]` and not in `requests[1]["prompt"]`. The "no prompt
changes" constraint is test-enforced, not merely stated.

### 2. One managed switch

`OLLAMA_STRUCTURED_OUTPUT`, **default disabled** for the first release.

The default is `False`, not `True`, because the schema's acceptance is endpoint-version-dependent
(§0) and this repository pins no Ollama version. Shipping it enabled by default would mean that on
an endpoint predating structured-output support, **every** photo and name analysis fails on the
first request after restart, degrading silently to `({}, "unavailable")` with a `logger.warning` as
the only signal. An operator flips it on after Step 0 passes on their endpoint. The default flips
to `True` in a later release, once a minimum Ollama version is a documented prerequisite in
`README.md`.

- `bourbonbook/config.py`: add `ollama_structured_output: bool = False` to `Settings`, placed with
  the other `ollama_*` fields (after `ollama_text_num_ctx`, `config.py:43`).
- Parse it in `from_env` with a dedicated helper. The helper:
  - treats **unset or empty** as the default (`False`) — every other setting in `from_env` does,
    including the adjacent `ollama_text_num_ctx` (`config.py:104-106`) whose `.env.example:15`
    line is deliberately blank. Raising on `""` would turn a blank `env_file` line or a
    `${VAR}` interpolation that resolves empty into a container crash-loop, for a feature flag;
  - **strips surrounding double quotes** before matching, because the managed writer emits
    `OLLAMA_STRUCTURED_OUTPUT="true"` (`admin_config.py:501`) and that file is fed to the container
    as `--env-file` (`run-docker.sh:15`) / `env_file` (`compose.yaml:11-13`). Without this,
    `Settings.from_env(include_managed=False)` — called at `bourbonbook/main.py:2125` and `:2154`
    to build the admin page's environment-baseline column, on a path where managed overrides are
    *excluded* so the raw quoted value reaches the parser — would raise, and the admin config page
    would return 500 while the app itself booted fine;
  - then trims and case-folds, and accepts exactly `true` or `false`;
  - raises `ValueError` naming `OLLAMA_STRUCTURED_OUTPUT` for any other **non-empty** value.
  - Accepted: `"true"`, `"True"`, `"TRUE"`, `" false "`, `'"true"'`, and unset/`""` (→ default).
    Rejected: `"1"`, `"yes"`, `"on"`, `"maybe"`.

  **Stated convention, so the next setting is not a coin flip.** The repository now has two
  boolean grammars and this is the rule: the **admin form** parser (`_parse_field`,
  `admin_config.py:550-558`) stays exact-lowercase-`true`/`false`, because it validates a value a
  human just typed into a `<select>` and a typo there must be visible immediately. The
  **environment** parser is forgiving of case, whitespace, and the managed writer's quoting, and
  treats unset as the default, because it consumes machine-written files and must never crash a
  container on a formatting nit. It is strict only about *unrecognised non-empty* values, which is
  where `SECURE_COOKIES` (`config.py:97`), `PROXY_HEADERS`, and `EMAIL_VERIFICATION_REQUIRED` are
  wrong today — they silently coerce a typo to `false`. A15 does not retrofit those three; see
  "Out of scope".
- `bourbonbook/admin_config.py`: add
  `ConfigField("OLLAMA_STRUCTURED_OUTPUT", "ollama_structured_output", "Ollama structured output",
  "Analysis", "boolean")` in the Analysis group, adjacent to the context-window fields
  (`admin_config.py:42-80`). Do **not** add it to `ENV_ONLY_SETTINGS`.
- `.env.example`: add a comment line **plus an uncommented key at column 0**, exactly the
  `OLLAMA_NUM_CTX` shape at `.env.example:8-9`, beside the other `OLLAMA_*` keys
  (`.env.example:8-15`):

  ```
  # Constrain analysis output with an explicit JSON Schema. Requires an Ollama build with
  # structured-output support on /api/generate; probe the endpoint before enabling.
  OLLAMA_STRUCTURED_OUTPUT=false
  ```

  A commented-out key would **not** match `^([A-Z0-9_]+)=`
  (`tests/test_config_registry.py:39`) and would fail
  `test_every_registered_field_is_documented_in_env_example` (`:81`) — a test criterion 7 forbids
  editing.
- `README.md`: one settings-table row near `README.md:126-130`, and one sentence near
  `README.md:176-178` stating that the setting requires an Ollama build with structured-output
  support and naming the version confirmed by Step 0.

### 3. The request path

Change only `bourbonbook/ollama.py::request_analysis`. Bind one photo predicate, resolve the switch
once, then:

```python
    has_photo = photo is not None
    output_fields = PHOTO_OUTPUT_FIELDS if has_photo else OUTPUT_FIELDS
    field_list = ", ".join(output_fields)
    structured = settings.ollama_structured_output
    payload: dict[str, Any] = {
        "model": model,
        "prompt": f"{prompt}\nReturn ONLY one JSON object with these keys: {field_list}.",
        "stream": False,
        "think": False,
        "format": analysis_schema(photo=has_photo) if structured else "json",
        "options": {
            "temperature": 0.1,
            "num_ctx": analysis_context_window(settings, photo),
        },
    }
```

- `has_photo` replaces the two spellings of the same predicate. Round-1 wrote
  `analysis_schema(photo=photo is not None)` one line above
  `PHOTO_OUTPUT_FIELDS if photo else OUTPUT_FIELDS`. They agree for every `Path` — no `Path` is
  falsy — so nothing was broken, but §1's whole contract is "schema membership equals the
  prompt/filter tuple" and writing the selecting predicate two ways in one payload undercuts it.
  One name, used in both places. This is behaviour-identical to `419d248`.
- **`temperature` stays `0.1` in both switch states.** Round-1 moved it to `0.0` when the schema
  was on. That bundled two independent changes behind one flag: the before/after diagnostic would
  have compared `{json, 0.1}` against `{schema, 0.0}`, making any accuracy or latency delta
  unattributable, and a temperature change alone moves value selection for `proof`/`abv`. The
  grammar constrains shape, not which number is emitted, so "sampling entropy buys nothing" was
  simply not true. Temperature is out of A15's scope; if it should be zero, that is a separate,
  separately-measured change.
- Disabled: byte-for-byte the pre-A15 payload — `"json"`, temperature `0.1`, same prompt, same
  `num_ctx` expression, same key order. Enabled: identical except `format`.
- `import` `analysis_schema` alongside the existing `from bourbonbook.analysis import (...)` block
  at `bourbonbook/ollama.py:13-19`. No new module-level dependency.
- Beyond §5 and §6, nothing else in the function changes: logging (`:135-144`, `:166-176`), usage
  recording (`:156-165`), and the return `normalize_analysis(values), "complete"` (`:177`) stay as
  they are.

### 4. Schema rejection: one request, no unconstrained retry

Do **not** retry a schema-rejected request without the schema. That would disguise a broken
contract as a successful analysis, double GPU work, and make the switch useless as an operator
control.

**`error_type` does not change.** Round-1 introduced `error_type = "schema_rejected"` on
"switch enabled AND `httpx.HTTPStatusError` AND status 400". That classification is over-broad:
Ollama returns 400 for other request-level faults, notably an undecodable or oversized image, which
is a live possibility here because `bourbonbook/ollama.py:130` base64s a user-uploaded file. Those
would have been reported as `schema_rejected`, sending the operator to flip a switch that fixes
nothing — while simultaneously *destroying* the only discriminator, since round-1 also forbade
recording any body or token that could tell the two apart. Keep `error_type = bounded_error_type(exc)`
(`bourbonbook/ollama.py:179`), which yields `provider_error` for `HTTPStatusError`
(`bourbonbook/observability.py:109-110`).

Express the hypothesis in the log context only. In the existing `except` block
(`bourbonbook/ollama.py:178-210`), after `failure_context(...)` (`:181`), when **all** of: the
switch is enabled, the exception is `httpx.HTTPStatusError`, and `exc.response.status_code == 400`
— set `context["failure_kind"] = "schema_rejected_or_bad_request"`. The name states the ambiguity
rather than hiding it. `failure_context` (`bourbonbook/ollama.py:73-112`) already emits only
provider, operation, model, endpoint scheme/host/port, failure kind, exception class name, HTTP
status, and duration — no bodies. Add no new log or metric field.

Every other failure keeps its current classification and `failure_kind`. Never log or record the
schema object, the prompt, the raw response, the server error body, image bytes, or URL
credentials.

The failure then follows the existing safe-degradation path unchanged: `return ({}, "unavailable")`
(`:210`), the photo and the row are kept, and the review form stays available for manual entry.

Do not add undocumented sampling knobs (`seed`, `num_predict`, `top_p`), do not hard-code a model
name or host, and do not make any live accuracy measurement a completion criterion.

### 5. Empty strings must not be persisted (forced by the schema)

Round-1 asserted that "a schema that requires every key and permits `null` therefore adds no
downstream noise". That is wrong, and it is a silent-data-loss bug the schema *creates*.

`bourbonbook/ollama.py:155` drops `None`, not `""`. `apply_analysis` (`bourbonbook/main.py:471`)
guards only `value is None`:

```python
        if not hasattr(bottle, key) or value is None:
            continue
```

so `""` is `setattr`'d over an existing column. Today a model may simply omit `brand`, `release`,
`mash_bill`, or `warehouse` and the stored value survives. Under `"required"` listing every
property with `{"type": ["string","null"]}`, the grammar forces a token at every key, and `""` is
as legal an answer as `null` — a routine constrained-decoding outcome. On
`POST /bottles/{id}/analyze` in `mode=photo`, that silently blanks user-entered fields.
`merge_analysis` (`bourbonbook/analysis.py:141-147`) and `missing_fields` (`:132`) both already
treat `""` as absent, so the inconsistency is confined to the persistence path.

A15 fixes it in both places:

- `bourbonbook/ollama.py:155` — filter on `parsed.get(key) not in (None, "")`.
- `bourbonbook/main.py:471` — skip when `value is None or value == ""`. Keep the `hasattr` guard
  and the `NUMERIC_ANALYSIS_FIELDS` branch below it unchanged.

Both edits are safe with the switch off and are covered by their own tests and by acceptance
criterion 13.

### 6. The `thinking` channel must not silently bypass the constraint

`raw_output = body.get("response") or body.get("thinking")` (`bourbonbook/ollama.py:153`) is a real
observed shape, codified by `tests/test_ollama.py:44`, and the default model `qwen3.6:35b`
(`README.md:172`) is thinking-capable. Ollama applies the grammar to the **response** channel only.
If a schema-constrained request returns an empty `response` and JSON in `thinking`, the code parses
**unconstrained** output while the operator believes the schema is in force — the worst of both
worlds, and precisely the case in which every claim this plan makes about shape is false.

With `settings.ollama_structured_output` **enabled**, `request_analysis` reads only
`body.get("response")`. An empty or missing `response` is a failure: it takes the existing
`except`/degradation path via the existing `TypeError` from `json.loads(None)` — no new exception
type, no new branch beyond the channel selection — with
`context["failure_kind"] = "empty_response_channel"`. With the switch **disabled** the
`or body.get("thinking")` fallback is preserved byte-for-byte, so
`test_qwen_thinking_field_is_accepted` (`tests/test_ollama.py:44`) passes unmodified.

### 7. `ocr_text` on the name path, and the catalog-MSRP route it opens

`analysis_schema(photo=False)` covers `OUTPUT_FIELDS`, which includes `ocr_text`
(`bourbonbook/analysis.py:39`), for calls that have **no image**: `analyze_bottle_name`
(`analysis.py:311`) and the refinement round-trip in `_refine_analysis` (`analysis.py:283`).

`ocr_text` stays a required property on the name schema. Dropping it would take the name schema to
18 properties, break membership parity with `OUTPUT_FIELDS`, and desynchronise the schema from the
prompt key list at `bourbonbook/ollama.py:123` — which is unchanged and test-enforced. It is not a
bug that it is there.

But it must be **null-only** there (`{"type": "null"}`, per §1), because a required nullable string
on a text-only call is an instruction to fabricate. `enrich_from_verified_catalog`
(`analysis.py:150-159`) matches on `values.get("ocr_text")` through `verified_product_from_text`
(`bourbonbook/catalog.py:205-213`), which does **substring** alias matching, and through
`verified_product`, which falls back to `fuzzy_verified_product` (`catalog.py:202`). A hallucinated
`ocr_text` containing any verified alias therefore flips status to `"verified"`, and
`bourbonbook/main.py:2819` and `bourbonbook/bottle_processing.py:105` call
`apply_analysis(..., allow_msrp=analysis_status == "verified")` — so a fabricated OCR string can
write a catalog MSRP onto the bottle. That path exists today (the prompt already asks for
`ocr_text` on the name path); the schema would materially raise its probability by making the field
mandatory. The null-only spec closes it. Acceptance criterion 14 pins the outcome.

## Implementation sequence

0. **Step 0 (§0).** Probe the operator's endpoint. Record the result and the Ollama version, and
   select the schema representation. Do not proceed without it.
1. Branch from `origin/main` at `419d248` or later. Re-read `bourbonbook/ollama.py:115-210` and
   `bourbonbook/analysis.py:19-41` before editing; this plan's line numbers are pinned to `419d248`.
2. `bourbonbook/analysis.py`: add `ANALYSIS_STATUS_VALUES`, `ANALYSIS_FIELD_SPECS`,
   `ANALYSIS_SCHEMA_FIELD_ORDER`, `analysis_schema`, and `_validate_analysis_field_specs()` —
   called **both** at import and as `analysis_schema`'s first statement. Add the schema tests
   below, including the golden snapshots.
3. `bourbonbook/config.py`, `bourbonbook/admin_config.py`, `.env.example`, `README.md`: add the
   setting through all three registry edits at once, then the parse tests.
4. `bourbonbook/ollama.py`: switch `format` behind the flag (temperature unchanged); tighten the
   empty-string filter (§5); gate the `thinking` fallback (§6); set the ambiguous `failure_kind`
   (§4). `bourbonbook/main.py:471`: skip `""` (§5). Nothing else in either file.
5. Extend `tests/test_ollama.py` with injected-client payload assertions for enabled, disabled,
   rejected, empty-string, and `thinking`-channel requests.
6. `docs/adr/plan.md`: add the A15 tracker row after `docs/adr/plan.md:98` and its action section,
   including the Step 0 outcome and the minimum Ollama version.
7. Run `make pr-review` (`Makefile:108` — `check` plus the production image build). Update
   `docs/architecture/components/ai-analysis.md` as-built only after the change merges.

## Tests

Deterministic, offline, injected fakes only. No test may contact an Ollama endpoint.

### `tests/test_analysis.py`

- `test_analysis_schema_membership_matches_the_output_field_tuples` — `set(schema["properties"])`
  equals `set(OUTPUT_FIELDS)` / `set(PHOTO_OUTPUT_FIELDS)`; `required` equals
  `list(schema["properties"])`; `date_bottled` is present and last on the photo schema and absent
  from the name schema; `ocr_text` is **last** on both (Round-4 revision R4-2).
- `test_analysis_schema_property_order_is_the_pinned_order` — `list(schema["properties"])` equals
  the literal expected list, written out in full in the test, for both paths. The order is a
  deliberate design decision (§1), so it is pinned as data rather than recomputed from the tuples.
- `test_analysis_schema_types_are_nullable_and_msrp_is_null_only` — `proof`/`abv` are
  `["number","null"]`, `fill_level` is `["integer","null"]`, every textual field and `date_bottled`
  are `["string","null"]`, `msrp` is `{"type": "null"}`, and `additionalProperties is False`.
- `test_analysis_schema_ocr_text_is_nullable_on_photo_and_null_only_on_name` — the photo schema's
  `ocr_text` is `{"type": ["string","null"]}`; the name schema's is `{"type": "null"}` (§7).
- `test_analysis_schema_status_enum_matches_the_normalizer_vocabulary` — the `status` enum is
  `["Unopened","Opened","Empty", None]`, and `set(enum) - {None}` equals
  `set(ANALYSIS_STATUS_VALUES)`, which equals the set of values `normalize_analysis` assigns.
  (`normalize_analysis` never assigns `None`; comparing the raw enum would fail.)
- `test_analysis_schema_matches_the_golden_snapshot` — the full serialized schema for **both**
  paths compared against a literal expected `dict` written out in the test file. This is the only
  test that makes a schema change visible in review; the type-table tests above check the literals
  the plan itself specifies and would pass an internally-consistent schema that no runtime accepts.
  Runtime acceptance is Step 0's job, not a unit test's — this repository has no `jsonschema`
  dependency (`pyproject.toml` `dependencies` and `dependency-groups.dev`), and A15 does not add
  one. See "Out of scope" for the deferred `jsonschema.Draft202012Validator.check_schema` option.
- `test_analysis_schema_fails_fast_on_field_set_drift` — with
  `monkeypatch.setattr(bourbonbook.analysis, "ANALYSIS_FIELD_SPECS", {...})` (an orphan key added)
  and, separately, `monkeypatch.setattr(bourbonbook.analysis, "PHOTO_OUTPUT_FIELDS", (...))` (an
  unspecified field added), a direct call to `bourbonbook.analysis.analysis_schema` raises
  `ValueError` naming the offending field. **Call `analysis_schema` directly, not through
  `request_analysis`** — `bourbonbook/ollama.py:13-19` imports `PHOTO_OUTPUT_FIELDS` as a bound
  name, so monkeypatching `bourbonbook.analysis.PHOTO_OUTPUT_FIELDS` does not change
  `request_analysis`'s `output_fields`, and schema and prompt would diverge under the patch. This
  test is the reason §1 forbids default arguments, import-time precomputation, and `@lru_cache` on
  `analysis_schema`, and the reason `analysis_schema` calls `_validate_analysis_field_specs()` as
  its first statement rather than relying on the import-time call alone.
- `test_analysis_schema_returns_an_isolated_copy_per_call` — mutating the returned tree (including a
  nested property dict and the `required` list) leaves the next call's result untouched, and two
  calls share no nested object identity.

The import-time call of `_validate_analysis_field_specs()` is exercised implicitly by every test
module importing `bourbonbook.analysis`; no separate test is needed for it, and no test may reload
the module. The drift test above covers the same helper through its per-call call site, which is
the only site a monkeypatch can reach.

### `tests/test_ollama.py`

The existing `FakeResponse` (`tests/test_ollama.py:18-26`) has a no-op `raise_for_status()` and no
`status_code`. The HTTP-error tests below need a **new** double. Construct the exception explicitly:

```python
class FailingResponse:
    def __init__(self, status: int) -> None:
        self.status_code = status

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "http://ollama.test/api/generate")
        raise httpx.HTTPStatusError(
            "error", request=request, response=httpx.Response(self.status_code, request=request)
        )
```

Anything less specific — a `FakeResponse` subclass with `status_code = 400` whose
`raise_for_status` raises a bare `httpx.HTTPError` — is still caught at `bourbonbook/ollama.py:178`,
still returns `({}, "unavailable")`, and would pass most assertions **without ever reaching the new
branch**. The `exc.response.status_code` access in §4 requires a real `HTTPStatusError` carrying a
real `httpx.Response`.

- `test_structured_output_sends_the_schema` — an injected client captures the request JSON for one
  photo and one name call: `payload["format"]` equals `analysis_schema(photo=True)` /
  `analysis_schema(photo=False)`, `payload["options"]["temperature"] == 0.1` in both, and
  `payload["options"]["num_ctx"]` still follows the configured role windows.
- `test_structured_output_disabled_preserves_the_legacy_payload` — with the setting false,
  `payload["format"] == "json"`, `payload["options"] == {"temperature": 0.1, "num_ctx": <role
  window>}` exactly (two keys, no `seed`, no `num_predict`), and the prompt is unchanged. With
  temperature no longer moving, this and the previous test differ **only** in `format`.
- `test_structured_output_leaves_the_prompt_and_field_list_unchanged` — with the setting either way,
  the prompt still ends with the key list, `date_bottled` appears in the photo prompt and not in the
  name prompt. This duplicates the guarantee already held by
  `test_photo_and_name_analysis_select_their_configured_models` (`tests/test_ollama.py:63`), which
  must keep passing unmodified.
- `test_schema_rejection_makes_one_request_and_falls_back_to_manual_review` — the injected client
  raises the `FailingResponse(400)` exception above for an enabled-schema request: exactly **one**
  POST is recorded, the result is `({}, "unavailable")`, and the captured log record's
  `extra["failure_kind"]` is `schema_rejected_or_bad_request` while `extra["error_type"]` remains
  `provider_error`. **Assert against the log record, not a usage row.** Nothing in
  `tests/test_ollama.py` installs a usage recorder, so `current_usage_recorder()`
  (`bourbonbook/observability.py:206`) returns `None` in every existing test and the
  `recorder.record(...)` block at `ollama.py:182-192` is skipped entirely — there is no row to
  read. The log-capture pattern already exists at `tests/test_ollama.py:189-210`. (Recording would
  require entering `observability.usage_context(recorder, user_id)` (`observability.py:195-203`)
  with an `AIUsageRecorder` — which needs a `session_factory`, `observability.py:225` — or a
  duck-typed stub, before `asyncio.run`. A15 does not add that fixture.)
- `test_no_raw_material_is_logged_on_the_rejection_path` — no captured log record contains the
  schema, the prompt, the response body, the server error text, image data, or URL credentials.
- `test_a_non_schema_http_error_keeps_its_existing_classification` — HTTP 500 with the switch
  enabled, and HTTP 400 with the switch disabled, both yield `error_type == "provider_error"` and a
  `failure_kind` that is **not** `schema_rejected_or_bad_request`.
- `test_empty_strings_are_dropped_from_the_parsed_values` — the injected client returns a
  schema-shaped body in which every property is `""`; `request_analysis` returns `{}`, and a
  companion test in `tests/test_main.py` asserts that `apply_analysis` with an all-empty-string
  analysis mutates **no** column on an existing populated `Bottle` (§5, criterion 13).
- `test_thinking_channel_is_ignored_when_structured_output_is_enabled` — with the switch on and a
  body of `{"response": "", "thinking": '{"name":"Example Bourbon"}'}` (the exact shape at
  `tests/test_ollama.py:20-26`), the result is `({}, "unavailable")` and `failure_kind` is
  `empty_response_channel`. `test_qwen_thinking_field_is_accepted` (`:44`) must pass **unmodified**
  with the switch off (§6).
- `tests/test_ollama.py:122` (`test_ollama_context_windows_follow_the_model_role`),
  `tests/test_ollama.py:225` and `:258` (the `warm_vision_model` payload tests) must pass unmodified.

### `tests/test_runtime_boundaries.py`

- Extend `test_settings_from_environment_parses_and_normalizes` with
  `OLLAMA_STRUCTURED_OUTPUT=true`.
- `test_ollama_structured_output_parses_the_accepted_vocabulary` — parametrized: `"true"`,
  `"TRUE"`, `"True"`, `" false "`, and the managed-writer form `'"true"'` all parse to the expected
  bool; `"1"`, `"yes"`, `"on"`, `"maybe"` raise `ValueError` whose message names
  `OLLAMA_STRUCTURED_OUTPUT`.
- `test_ollama_structured_output_treats_unset_and_blank_as_the_default` — absent from the
  environment, and set to `""`, the setting is `False` and **no exception is raised**.
- `test_ollama_structured_output_defaults_to_disabled` — the `Settings` dataclass default is
  `False`.

### `tests/test_config_registry.py`

Unmodified. It must pass on the strength of the three coordinated edits alone. If it fails, an edit
is missing — do not touch the test. In particular the `.env.example` line must be uncommented and
at column 0 (§2).

### `tests/test_admin.py`

Round-trip the field through the managed-config surface: setting it to `false` and reading back
through `read_managed_config` yields `False`; submitting `maybe` is rejected with a message naming
the key (`bourbonbook/admin_config.py:555-558`); the value is not treated as a secret and is
displayed in full. **Do not assert the unquoted byte sequence against the file contents** —
`write_managed_config` emits `f"{field.key}={json.dumps(values[field.key])}"`
(`admin_config.py:501`), so the line on disk is `OLLAMA_STRUCTURED_OUTPUT="false"` with literal
quotes, which `_decode_value` (`:369-374`) strips on read. Assert the round-tripped value, or quote
the real serialization.

Add `test_admin_config_page_renders_with_a_quoted_managed_boolean` — the admin page's
environment-baseline column calls `Settings.from_env(include_managed=False)`
(`bourbonbook/main.py:2125`, `:2154`) with `OLLAMA_STRUCTURED_OUTPUT="true"` in `os.environ`; the
page must render 200, not 500 (§2's quote-stripping rule).

### `tests/test_catalog_extract.py`, `tests/test_ollama_search.py`

Unmodified and still passing — proof that the change did not leak into the other Ollama payloads
(`tests/test_catalog_extract.py:109` asserts `{"temperature": 0, "num_ctx": 32768}`;
`tests/test_ollama_search.py:156` asserts `{"num_ctx": 4096}`).

## Acceptance criteria

An engineer is held to all of the following.

1. With the switch enabled, the photo request's `format` is exactly `analysis_schema(photo=True)`
   (20 properties, `ocr_text` first, `date_bottled` last) and the name request's is exactly
   `analysis_schema(photo=False)` (19 properties, `ocr_text` first and null-only, no
   `date_bottled`). Both list every property in `required`, set `additionalProperties: false`, use
   the type/enum/null rules tabulated in §1 (as amended by Step 0's selected representation), and
   match their golden snapshots.
2. `options` is `{"temperature": 0.1, "num_ctx": analysis_context_window(settings, photo)}` in
   **both** switch states — two keys, the `num_ctx` expression unchanged, temperature unchanged
   from `419d248`.
3. With the switch disabled, the request is byte-equivalent to `419d248`: `"format": "json"`,
   `{"temperature": 0.1, "num_ctx": <role window>}`, identical prompt, and the `thinking` fallback
   at `bourbonbook/ollama.py:153` intact.
4. The prompt string at `bourbonbook/ollama.py:123` is unchanged in both states, and
   `tests/test_ollama.py:63` passes unmodified.
5. The tuple/specification parity check is a single helper, `_validate_analysis_field_specs()`,
   invoked **both** at import of `bourbonbook.analysis` **and** as the first statement of
   `analysis_schema`. It raises `ValueError` naming the field when the two disagree in either
   direction. `analysis_schema` resolves both globals on every call, is not cached, and returns a
   deeply isolated copy per call. Because the import-time call makes drift impossible to carry into
   a running process, the per-call call can fire only under a test monkeypatch, so no `ValueError`
   from schema construction can reach an HTTP handler.
6. `warm_vision_model`, `catalog_extract.py`, and `ollama_search.py` payloads are untouched, proven
   by their existing tests passing unmodified.
7. `OLLAMA_STRUCTURED_OUTPUT` exists as a `Settings` field, a `ConfigField` in the Analysis group,
   an **uncommented** `.env.example` key at column 0, and a README row;
   `tests/test_config_registry.py` passes unedited; it is absent from `ENV_ONLY_SETTINGS`; it is
   not marked secret.
8. Environment parsing treats unset and empty as the default, strips surrounding double quotes,
   accepts trimmed case-insensitive `true`/`false`, and raises a `ValueError` naming the key only
   for a non-empty unrecognised value. The `Settings` default is `False`. The admin config page
   renders 200 with a JSON-quoted managed boolean in `os.environ`.
9. A schema-enabled `/api/generate` HTTP 400 produces exactly one POST, `error_type ==
   "provider_error"`, `failure_kind == "schema_rejected_or_bad_request"`, and the existing
   `({}, "unavailable")` return. No second, unconstrained request is issued.
10. No log record, usage row, or metric emitted on that path contains the schema, the prompt, the
    response body, the server error text, image data, or URL credentials.
11. No database migration is added; `bourbonbook/migrations.py:16` still reads
    `HEAD_REVISION = "0010_bottle_lifecycle_dates"`.
12. `make pr-review` passes. No deterministic test contacts an Ollama endpoint.
13. An all-empty-string, schema-shaped response mutates **no** column on an existing populated
    bottle: `request_analysis` drops `""` (`bourbonbook/ollama.py:155`) and `apply_analysis`
    (`bourbonbook/main.py:471`) skips `""`.
14. With the switch enabled, a name-path response cannot reach `analysis_status == "verified"`
    through `ocr_text`: the name schema's `ocr_text` is null-only, so
    `enrich_from_verified_catalog` can only match on `name`.
15. With the switch enabled, a body whose `response` is empty is a failure with
    `failure_kind == "empty_response_channel"`; the `thinking` channel is never parsed. With the
    switch disabled, `test_qwen_thinking_field_is_accepted` passes unmodified.
16. The PR description records the Step 0 result: the endpoint's Ollama version, which of P1-P4
    passed, and which schema representation was selected.

Explicitly **not** acceptance criteria: any accuracy number, any benchmark report. See ADR 0003.
Step 0's compatibility result is a **pre-deploy prerequisite**, not an accuracy gate — it records
whether the runtime accepts the payload at all, which is a correctness question, not a quality one.

## Operational verification and rollback

Step 0 (§0) has already established that the endpoint accepts the schema. After the deterministic
gates pass, and with explicit operator approval, run one further minimal probe with the **final**
merged schema and the selected vision model, and record:

- pass/fail, the model identifier, and safe timing/HTTP metadata; and
- **p95 wall-clock duration over at least 10 photo requests, with the switch on and off.** This is
  mandatory, not advisory. §1's ordering rationale explains why: all-required schemas force the
  model to emit every property before EOS, and the client timeout is a hard 120s
  (`bourbonbook/provider_clients.py:50`) with no `num_predict`. **Stated ceiling: if enabled-p95
  exceeds 60s — half the client timeout — do not enable the switch in production.** A p95 above
  that leaves no headroom for a slow image and turns a shape improvement into a stream of
  `error_type="timeout"` failures. This observation is the substitute for the `compare_reports`
  latency check that the diagnostic section downgrades; the check is not simply removed.

**Rollback is not an environment variable.** Because managed values outrank the environment
(`bourbonbook/config.py:90`) and `write_managed_config` pins every persisted `CONFIG_FIELDS` key
(`bourbonbook/admin_config.py:495-504`), exporting `OLLAMA_STRUCTURED_OUTPUT=false` does nothing on
any system whose admin UI has been used. Roll back by setting the field to `false` **in the admin
config UI**, or by editing `DATA_DIR/.env` (`data/.env`) directly, then restarting. There is no
data migration and no state to unwind. The manual-review guarantee holds in every state.

## Out of scope — known limitations, recorded deliberately

These are real and are not fixed by A15.

- **No Ollama version pin in the repository.** A15 records the version Step 0 observed on the
  operator's endpoint in `docs/adr/plan.md` and `README.md`, but adds no enforced floor and no
  startup compatibility probe. A startup probe alongside `warm_vision_model`
  (`bourbonbook/ollama.py:234-240`) that disables the schema for the process on a 400 is the right
  long-term shape; it is deferred because it adds a network call to the boot path, which is a
  separate design decision from a provider-contract change. Mitigated meanwhile by the
  default-disabled switch (§2).
- **`schema_rejected` is not distinguishable from other 400s.** §4 deliberately reports
  `failure_kind = "schema_rejected_or_bad_request"` rather than guessing. Narrowing it correctly
  requires a bounded, fixed-vocabulary token derived from the response body (never the raw body),
  which means extending `failure_context` (`bourbonbook/ollama.py:73-112`) and its privacy review.
  Deferred.
- **`SECURE_COOKIES` (`bourbonbook/config.py:97`), `PROXY_HEADERS`, and
  `EMAIL_VERIFICATION_REQUIRED` still coerce a typo to `false` silently.** §2 states the convention
  A15 follows and that these three do not. Retrofitting them changes boot behaviour for three
  security-relevant settings and belongs in its own action with its own review. Deferred.
- **No JSON-Schema validity assertion in the test suite.** `jsonschema` is not a dependency
  (`pyproject.toml`), and A15 does not add one. Runtime acceptance is covered by Step 0 and by the
  golden snapshots making any schema change visible in review. Adding `jsonschema` to the dev
  dependency group and asserting `Draft202012Validator.check_schema(analysis_schema(photo=True))`
  is a cheap follow-up; it checks *draft* validity, not llama.cpp grammar convertibility, so it
  would not have caught the risk Step 0 addresses.
- **Generation length is unbounded.** A15 adds no `num_predict`, per the "no undocumented sampling
  knobs" rule. The mandatory p95 observation above is the control. If the ceiling is breached, the
  follow-up is a `num_predict` setting, designed and measured on its own.
- **Temperature stays at `0.1`.** Whether constrained decoding should also run at temperature zero
  is an open question A15 deliberately does not answer, because bundling it would make the
  before/after diagnostic unattributable (§3). It needs its own third measurement run.

---

## Optional: post-merge accuracy diagnostic — NON-BLOCKING

**This section gates nothing.** Nothing in it is an acceptance criterion, a merge condition, a
deployment condition, or a rollback trigger. A regression measured here is information for the
operator, not a blocker. ADR 0003 retired the benchmark gate but deliberately kept the tooling
"for ad hoc, non-blocking use" (Decision 4 and its Consequences section); this is that use.

A JSON Schema constrains response *shape*. Whether it also improves *extraction* is an empirical
question the schema itself cannot answer. The operator may answer it after merge, on their own
hardware, with the existing CLI.

### Procedure

All three commands refuse to read or write outside `DATA_DIR/benchmarks`
(`private_benchmark_path`, `bourbonbook/benchmark_cli.py:549-556`).

**Before anything else: the switch must be flipped in `data/.env`, not in the shell.**
`make benchmark-run` executes `uv run --env-file data/.env` (`Makefile:73`) — the same managed file
that `load_managed_overrides` reads and that outranks `os.environ` (`bourbonbook/config.py:90`). A
shell `export OLLAMA_STRUCTURED_OUTPUT=false` is ignored twice over, and both the "before" and
"after" runs would measure the **same** setting while producing a table that looks valid and means
nothing. Edit `data/.env` (or use the admin UI, which writes it) between the two runs, and confirm
the value took effect by reading it back before each run.

1. **Export once.** `make benchmark-export BENCHMARK_OWNER=<owner>` →
   `bourbonbook.benchmark_cli export` (`benchmark_cli.py:102-160`), writing
   `data/benchmarks/fixtures/collection-v1`. The destination must be new or empty
   (`:105-106`), and every exported bottle must have a photo (`:125-128`).
2. **Curate the fixture** (see the caveat below), then leave it frozen. Both runs must use the same
   fixture: `compare` fails on `fixture manifest differs` when
   `fixture_manifest_sha256` diverges (`benchmark_cli.py:502-503`).
3. **Run "before".** Set `OLLAMA_STRUCTURED_OUTPUT=false` in `data/.env`, then
   `make benchmark-run BENCHMARK_CANDIDATE=data/benchmarks/reports/before-schema.json`.
4. **Run "after".** Set `OLLAMA_STRUCTURED_OUTPUT=true` in `data/.env`, restart, and repeat into
   `after-schema.json`.

   Hold the run parameters fixed. `make benchmark-run` (`Makefile:71-79`) passes only `--fixture`,
   `--output`, `--live`, `--preprocess-revision`, and `--cold-start-state
   $${COLD_START_STATE:-uncontrolled}`. It never passes `--runs` or `--operations`, so **their
   defaults are already identical between the two runs and nothing needs doing**. The one variable
   the operator controls is the `COLD_START_STATE` **environment variable** (not a flag) — set it
   to the same value for both runs, or leave it unset for both. `compare` rejects mismatches on
   runs-per-case and cold-start state (`benchmark_cli.py:504-507`). To vary `--runs` or
   `--operations`, invoke `python -m bourbonbook.benchmark_cli run` directly rather than through
   `make`, and pass the identical flags to both runs.
5. **Read the per-field table.** Each report carries
   `operations.<photo|name>.summary.fields.<field>` with `scored`, `matched`, and `accuracy`
   (`summarize`, `benchmark_cli.py:251-277`); `fill_level` additionally carries `mae`
   (`:265-266`, mean absolute error from `:235-248`).

Because §3 keeps temperature at `0.1` in both states, this comparison isolates the schema. That was
the point of unbundling it.

`make benchmark-compare` may be run for its structural checks, but **treat its pass/fail verdict as
advisory only**. `compare_reports` (`benchmark_cli.py:488-540`) fails on any p50/p95 latency
increase, which a constrained-decoding change can plausibly cause while extraction improves. Its
substitute is the **mandatory** p95 ceiling in "Operational verification and rollback" above; the
latency question is not dropped, it is moved somewhere it actually blocks.

### Report the per-field table, not `overall_accuracy`

`overall_accuracy` (`benchmark_cli.py:267-275`) is a single ratio pooled over every scored field of
every case. It is dominated by the many easy textual fields and will move barely at all even if the
schema fixes a hard field outright. Report the per-field rows instead, and lead with the four fields
the schema actually constrains beyond "a string":

| Field | Schema constraint | Why it should move |
|---|---|---|
| `proof` | `["number","null"]` | today `"107 proof"` is legal JSON and becomes `None` after normalization |
| `abv` | `["number","null"]` | same, for `"50.5% ABV"` |
| `fill_level` | `["integer","null"]` | today `"about half"` is legal and unusable; also read its `mae` |
| `status` | enum `Unopened`/`Opened`/`Empty`/`null` | today `"sealed"` passes normalization untouched (`analysis.py:242-243`) and is written to the column; under the schema the outcome becomes `null` |

Scoring tolerances, for reading the numbers honestly: `proof` and `abv` match within `0.5`,
`fill_level` within `10` (`matches`, `benchmark_cli.py:208-218`). Fields scored per operation are
`PHOTO_FIELDS` / `NAME_FIELDS` (`benchmark_cli.py:34-40`); `msrp` is never scored, and only
non-empty expected values are compared (`:451`).

A useful summary is one table: field, before `accuracy`, after `accuracy`, before/after `scored`
(they should be equal), and `mae` for `fill_level`.

### Caveat: exclude catalog-matched bottles from the fixture

`run_fixture` calls `analyze_bottle` (`benchmark_cli.py:421`, `:437`). `analyze_bottle`
(`bourbonbook/analysis.py:294-308`) calls the **provider first** (`:295`) and only then runs
`enrich_from_verified_catalog` (`:300`). On a catalog hit it returns immediately with status
`verified` (`:301-302`) and the values merged from the curated catalog record — including its MSRP.
So the model **is** invoked on those cases and they still cost a full GPU run; what they do not do
is score the model. They score the **catalog**, read identically before and after, dilute every
per-field accuracy toward the catalog's correctness, and can hide a real change on the
model-driven cases.

A case is at risk when the **model's own** returned `name` or `ocr_text` matches a verified record
— note that the match is on the model's output, not on the fixture's expected name. Two matchers
are involved and both must be screened:

- `verified_product` (`bourbonbook/catalog.py:193-202`) tries exact alias equality **and then falls
  back to `fuzzy_verified_product`** (`:202`). Screening on exact aliases alone will
  **under-exclude**.
- `verified_product_from_text` (`bourbonbook/catalog.py:205-213`) does **substring** alias matching
  against `ocr_text`.

Because the trigger is the model's output rather than the fixture's, exact pre-screening can only
approximate. The reliable signal is post-run: `status == "verified"` in the report samples.

To exclude them:

- prefer exporting from an owner whose bottles are not in the verified catalog; otherwise
- screen expected names through **`verified_product`** (which includes the fuzzy fallback), not
  through alias equality, and then
- delete those entries from `cases` in `data/benchmarks/fixtures/collection-v1/manifest.json`,
  update `case_count` to match, and recompute `manifest_sha256`. The digest is
  `benchmark_cli.json_digest` over the manifest **with `manifest_sha256` removed**
  (`benchmark_cli.py:65-67`, `:156`, verified on load at `:163-176`). Orphaned files under
  `photos/` are harmless — the loader only checks photos referenced by a listed case (`:172-175`).

After the run, sanity-check that no sample carries `status == "verified"`. If any does, the fixture
still contains a catalog-matched bottle and the per-field table for that case is not measuring the
model.

---

## Round-2 revisions

Changes made after senior engineering review, so a reader of round-1 can diff intent:

- **Step 0 added** as the first work item: an endpoint compatibility probe with four numbered
  sub-probes and two named fallback schema representations (R1, R2). The plan no longer asserts
  what Ollama accepts.
- **Default flipped to disabled** (§2), with the rationale and the condition for flipping it later.
- **Temperature unbundled** (§3): it stays `0.1` in both states, so the diagnostic isolates the
  schema.
- **Property order changed** (§1): `ocr_text` first, not tuple order, with the scratchpad and
  EOS-length rationale.
- **Parity check moved to import time** (§1), fixing both the "never runs when disabled" hole and
  the HTTP-500-on-drift hole. (Superseded in part by round 3 — see below.)
- **`error_type` no longer changes** (§4): `provider_error` is kept and the hypothesis lives in
  `failure_kind = "schema_rejected_or_bad_request"`.
- **Two new correctness sections**: §5 (empty strings are persisted today and the schema makes that
  likely) and §6 (the `thinking` channel bypasses the grammar).
- **§7 added**: `ocr_text` is null-only on the name path, closing the fabricated-OCR → false
  catalog match → MSRP write route.
- **Rollback and benchmark procedures corrected** for the managed-`.env`-outranks-environment
  contract, and the benchmark flag instructions corrected to what `make benchmark-run` actually
  passes.
- **Latency moved from "advisory" to a mandatory pre-enable p95 ceiling** in operational
  verification.
- **Test section rewritten** with the concrete `httpx.HTTPStatusError` fixture, log-record rather
  than usage-row assertions, the direct-call requirement for the drift test, and golden snapshots.
- **New "Out of scope" section** recording six deferred items explicitly.
- **Source review citation corrected** to the vault note; `docs/ollama-vision-review.md` does not
  exist.

## Round-3 revision

One finding, and it was introduced by the round-2 edit itself.

- **The parity check is now one helper called from two sites** (§1, §Tests, criterion 5). Round 2
  wrote "checked once, at import — not per call" and, in the same document, mandated
  `test_analysis_schema_fails_fast_on_field_set_drift`, which monkeypatches
  `bourbonbook.analysis.ANALYSIS_FIELD_SPECS` and `bourbonbook.analysis.PHOTO_OUTPUT_FIELDS`
  *after* import and expects a direct call to `analysis_schema` to raise `ValueError` naming the
  field. A monkeypatch cannot re-trigger an import-time check, and no test may reload the module.
  With an import-time-only check the orphan-key case raises nothing at all (an extra key in
  `ANALYSIS_FIELD_SPECS` is never read when properties are built from the tuple) and the
  missing-spec case raises `KeyError` from `ANALYSIS_FIELD_SPECS[field]`, not `ValueError`. The
  test failed in both directions and was unwritable as specified.

  The fix is `_validate_analysis_field_specs()`, called **both** at import of
  `bourbonbook.analysis` **and** as the first statement of `analysis_schema`. The HTTP-500
  objection is answered entirely by the import-time call: after a successful import the two globals
  are constant for the life of the process, so the per-call guard can fire only under a test
  monkeypatch and can never reach an HTTP handler. The "not per call" prohibition and the "was
  wrong twice" framing are removed; the "never runs when disabled" half of that argument stands and
  is now attributed to the import-time call specifically.


---

## Round-4 revision — 2026-08-23

Two defects were found by running the implemented code against the operator's own endpoint
(Ollama `0.32.13`, `qwen3-vl:8b`). Both invalidate a premise this plan reasoned from. The code has
been changed; **the sections above are superseded where they conflict with this one.**

### R4-1 — Section 6's premise is false; the channel is a model property

Section 6 asserted that Ollama "applies the grammar to the **response** channel only", so an empty
`response` under a schema means the grammar was not applied and parsing `thinking` would assert a
false guarantee. Measured, that is wrong:

| model | family | `response` | `thinking` |
| --- | --- | --- | --- |
| `qwen3-vl:8b` | qwen3vl | empty | valid, schema-conforming |
| `qwen3.8:27b` | qwen35 | empty | valid, schema-conforming |
| `qwen3.6:27b` | qwen35 | empty | valid, schema-conforming |
| `qwen2.5-coder:7b` | qwen2 | valid, schema-conforming | empty |

Which channel carries the constrained object is a property of whether the model is
thinking-capable, not of the schema. `think: false` does not suppress it on `qwen3-vl:8b` — the
literal `<think>` tag appears in the output. Section 6 as implemented failed **every** request on
the only endpoint this project targets, with `failure_kind = "empty_response_channel"`.

**Superseded by:** `raw_output = body.get("response") or body.get("thinking")` in **both** states —
the fallback is load-bearing for model portability, not a legacy quirk — plus two real guards that
replace the channel heuristic:

1. **Completeness.** A structured response is refused unless `done is True` **and**
   `done_reason == "stop"`, reported as `failure_kind = "incomplete_generation"`. A `done`-only
   guard is insufficient: a generation stopped by context exhaustion returns `done: true` with
   `done_reason: "length"` and a truncated string that may still parse.
2. **Conformance.** `validate_against_schema()` (`bourbonbook/analysis.py`) checks the parsed object
   against the schema that was actually sent — key set, types, enum membership — raising
   `SchemaConformanceError` (a `ValueError`), reported as `failure_kind = "schema_nonconforming"`.

Conformance validation is not belt-and-braces. Ollama's grammar converter **silently ignores**
`maxLength`: a schema carrying it produced byte-identical output to one without. "A schema was
sent" therefore does not imply "every keyword in it was applied", on any channel. The guarantee is
now checked rather than assumed, which is strictly stronger than what §6 attempted.

`error_type` remains `provider_error` throughout, per §4.

### R4-2 — `ocr_text` moves from first to last

§1 placed `ocr_text` first so the transcription would be generated before the fields it informs.
Measured, that ordering is unusable. The model reliably falls into a newline repetition loop inside
the `ocr_text` string; a grammar cannot break a loop inside a legal string value, so the server
aborts the generation mid-string and returns `done: false` with an unterminated object.

Same photo, same prompt, temperature 0.1, only the position of `ocr_text` differing:

| arm | runs | parsed |
| --- | --- | --- |
| `ocr_text` **first** | 10 | **0** |
| `ocr_text` **last** | 9 | **8** (all 20 keys, `done_reason: "stop"`, ~3.3 s) |

Last position does not prevent the loop — it means the other 19 fields are already emitted before
it can start. The transcription itself is not degraded.

**This defect pre-dates A15**: the same prompt with `format: "json"` fails identically, so it is a
latent production bug that structured output exposed rather than caused. It is fixed here because
A15 is unusable without it, and because the fix is a one-line ordering change rather than new
scope.

### Not addressed here

- The repetition loop still exists; only its blast radius is contained. A photo whose transcription
  runs away still loses `ocr_text` and fails the completeness guard. Bounding the transcription in
  `PHOTO_PROMPT` is prompt work, outside A15's fence, and belongs in a separate action alongside
  IMP-04.
- `maxLength` being ignored is recorded above as measured behaviour; no attempt is made to enforce
  length through the schema, because it demonstrably cannot be.
