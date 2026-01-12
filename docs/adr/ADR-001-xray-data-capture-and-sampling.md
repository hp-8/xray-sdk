# ADR-001: X-Ray Data Capture and Sampling

## Status
Accepted

## Context
Current SDK-side sampling computes stats on the sampled set, which can misstate rejection rates and per-reason counts under load. The SDK is synchronous and unbuffered, risking pipeline slowdowns when the API is slow. We need a lightweight, high-volume path that keeps stats correct, preserves reasoning diversity, and never blocks pipelines, while staying queryable across pipelines.

## Decision
- Canonical stats are computed on the full decision set **server-side** before any sampling; response returns `sampling_summary` (`total`, `kept`, `sampled`).
- API performs sampling using the shared `xray.sampler.DecisionSampler` (per-reason, hard cap via threshold/per_reason env vars). SDK sends decisions unsampled by default.
- SDK may later add buffered/async mode with drop counters; defaults remain synchronous and non-raising for backward compatibility.
- Input contracts: use `xray.models` for payload types; ingest validates sizes (`MAX_DECISIONS_PER_STEP`, `MAX_EVIDENCE_PER_STEP`) to avoid backend failures.
- Enforce `sequence_order` determinism server-side; evidence must align 1:1 with stored decisions or ingestion fails fast.

## Alternatives Considered
1. **SDK-only sampling and stats**: Rejected—stats become inaccurate when sampling occurs client-side and bandwidth is saved at the cost of observability.
2. **Store-all, no sampling**: Rejected—too heavy for 5k–50k candidate steps; violates lightweight requirement.
3. **Stats-only, no decisions**: Rejected—loses per-candidate reasoning needed for “why” debugging.

## Consequences
### Positive
- Accurate stats regardless of sampling.
- Predictable storage bounds with hard caps.
- Pipelines are non-blocking when backend is slow/unavailable.
- Better cross-pipeline queries via normalized step/reason vocab and sequence ordering.

### Negative
- Slightly higher API CPU for server-side sampling.
- More complexity (buffering, summaries, vocab validation).

### Risks
- Buffer overflow or dropped events: mitigate with metrics and drop counters.
- Misconfigured vocab causing rejects: mitigate with clear validation errors and defaults.

## Implementation Notes
- API: compute stats on raw decisions, then sample; store both `stats` and `sampling_summary`.
- API sampling: per-reason reservoir + head/tail preservation + hard cap; keep ordering by `sequence_order`.
- SDK: add buffered mode (queue + background worker), include `total_decisions` and `sequence_order`; pre-sampling optional but stats rely on totals.
- Query: expose `sampling_summary` and accurate counts; add JSON/JSONB indexes for rejection_rate filters.
- Config: env flags for sampling thresholds, caps, buffering size, and drop policy. Comments should reference this ADR (e.g., `# Implements ADR-001`).

## References
- ARCHITECTURE.md (data model, queryability, performance)
