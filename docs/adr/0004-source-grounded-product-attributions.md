# ADR 0004: Permit bounded automatic source-grounded product attributions

Status: Proposed
Date: 2026-08-25

## Decision

For `distilled_by` and `mash_bill` only, Bourbon Book may automatically persist and apply a
source-grounded result when an exact non-placeholder product identity, typed field result, public
canonical URL from the same provider-recorded search-result set, recorded title, bounded basis,
and field-specific evidence validate. It applies only to blank fields or `provider_recall`, never
to `user_entered`, `legacy_unknown`, or `verified_catalog` values.

SQLite caches field-level resolved/no-evidence outcomes for one year. Provider, transport, schema,
or source-validation failures neither cache nor alter values. Grounded sources are visible only to
the authenticated owner.

## Consequences

This is a narrow exception to the model-output proposal rule. It does not authorize automatic
recall, pricing, other fields, page fetches, fuzzy matching, or source governance changes.

## Alternatives considered

- User-confirmed proposals only: rejected because A16 requires automatic grounded attribution.
- Model recall as fact: rejected because it has no source-grounded evidence.
- Required page fetch: rejected because target pages may be unavailable.
- A combined product fact: rejected because producer and mash bill need independent evidence.
