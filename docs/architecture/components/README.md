# Bourbon Book: Component Design Docs

Detailed, per-component design documentation supporting the [HLDD](../hldd.md) and the
[C1-C4 architecture views](../c1-system-context.md). Each document covers one component's
responsibility, internals, key sequences, and the design properties worth preserving when changing
it.

| Component | Document |
| --- | --- |
| Identity, sessions & abuse guards | [identity-and-sessions.md](identity-and-sessions.md) |
| Persistence & migrations | [persistence-and-migrations.md](persistence-and-migrations.md) |
| Bottle, shopping-list & sharing workflow | [bottle-workflow.md](bottle-workflow.md) |
| AI analysis orchestration | [ai-analysis.md](ai-analysis.md) |
| Pricing & catalog | [pricing-and-catalog.md](pricing-and-catalog.md) |
| Catalog import pipeline | [catalog-import.md](catalog-import.md) |
| Model evaluation & benchmarking | [model-evaluation-and-benchmarking.md](model-evaluation-and-benchmarking.md) |
| Administration & configuration | [admin-and-configuration.md](admin-and-configuration.md) |
| Observability & operations | [observability-and-operations.md](observability-and-operations.md) |
| PWA shell & frontend | [pwa-frontend.md](pwa-frontend.md) |

See also:

- [HLDD: High-Level Design Document](../hldd.md)
- [ADR 0001: Current Architecture Baseline](../../adr/0001-current-architecture-baseline.md)
- [ADR 0002: Local-First Pricing Catalog](../../adr/0002-local-first-pricing-catalog.md)
- [ADR 0003: Fixed Local Model Selection, No Benchmark Gate](../../adr/0003-fixed-local-model-no-benchmark-gate.md)
- [ADR 0004: Source-Grounded Product Attributions](../../adr/0004-source-grounded-product-attributions.md) (Proposed)
- [ADR 0005: Drop OHLQ as a Grounded Price Source](../../adr/0005-drop-ohlq-as-a-price-source.md) (Proposed)
- [ADR 0006: LiteLLM Gateway as a Third Analysis Provider](../../adr/0006-litellm-gateway-provider.md) (Proposed)
