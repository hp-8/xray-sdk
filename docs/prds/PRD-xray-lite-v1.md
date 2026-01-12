# PRD: X-Ray Lightweight Capture v1

## 1. Product Overview
**Product Name**: X-Ray SDK/API  
**Description**: Lightweight, accurate decision-trace capture for high-volume pipelines.  
**Target Users**: Engineers instrumenting multi-step, non-deterministic pipelines (LLM + retrieval + filters).  
**Problem Statement**: Provide fast, non-blocking instrumentation that keeps stats accurate and makes LLM decision failures traceable even when candidate volume is large.

## 2. Goals and Success Criteria
**Primary Goal**: Maintain accurate decision stats and traceability while remaining lightweight at 5k–50k candidates per step.  
**Secondary Goals**: Non-blocking SDK operation; predictable storage bounds; cross-pipeline queryability.  
**Success Metrics**:
- Stats accuracy: rejection_rate deviation < 0.5% vs unsampled ground truth in test runs.
- SDK overhead: < 3% wall-clock increase on a 5k-candidate step in buffered mode.
- Dropped-event rate: < 0.1% in steady state; all drops counted and surfaced.

## 3. Non-Goals ⚠️
- Full web UI redesign (only minor mobile-first tweaks as needed).
- Multi-tenant auth/PII handling (future).
- Polyglot SDKs (Python only in v1).

## 4. User Personas
- **Pipeline Engineer (Medium-High)**: Needs “why” visibility without slowing jobs.
- **Data/ML Engineer (High)**: Runs ablations, needs accurate stats to compare steps.

## 5. Core User Flows
### Flow: Instrument a pipeline step at scale
1. Start run → record step with decisions (possibly large) → API computes stats and samples → user queries step with sampling_summary.  
End State: Accurate stats + sampled decisions stored; pipeline not blocked.  
Error Paths: API unavailable → SDK buffers then drops with counters; validation failure on vocab → clear error.

## 6. Feature List with Acceptance Criteria
### Feature: Accurate stats with sampling
- Inputs: Step payload with decisions and `total_decisions`.
- Outputs: Stored `stats` from full set; `sampling_summary {total, kept, sampled}` returned.
- Acceptance:
  - [ ] Stats reflect full decision set even when sampling is applied.
  - [ ] Response includes `sampling_summary` for every step with decisions.

### Feature: Server-side sampling with bounds
- Inputs: Sampling config (threshold, per_reason, head/tail, hard cap).
- Outputs: Sampled decisions stored; ordering preserved by sequence_order.
- Acceptance:
  - [ ] All accepted decisions kept; rejected sampled per reason with hard cap.
  - [ ] sequence_order preserved for sampled results.
  - [ ] Hard cap enforced and reflected in `sampling_summary.kept`.

### Feature: Buffered/non-blocking SDK mode
- Inputs: SDK config (buffer size, flush interval, backoff, drop policy).
- Outputs: Background flush; counters for queued, flushed, dropped.
- Acceptance:
  - [ ] When API is slow/unavailable, pipeline continues; drops counted and logged.
  - [ ] Buffer applies backoff and respects max in-flight; no unbounded growth.

### Feature: Query performance
- Inputs: Queries for steps with rejection_rate filters; decision queries.
- Outputs: Accurate counts; efficient filtering.
- Acceptance:
  - [ ] list_runs and decision queries use DB counts (no len(all()) in memory).
  - [ ] rejection_rate filters execute in SQL or indexed JSON/JSONB where supported.

### Feature: Storage guardrails for evidence/decisions
- Inputs: Evidence payloads, large decision sets.
- Outputs: Size/TTL enforcement; optional stats-only mode.
- Acceptance:
  - [ ] Evidence size limit and optional gzip applied; oversize rejected with clear error.
  - [ ] Configurable TTL or cap per pipeline; stats-only path when above cap.

## 7. Functional Requirements
- APIs Involved:
  - `POST /v1/runs/{id}/steps`: accepts `total_decisions`, computes stats pre-sampling, returns `sampling_summary`.
  - Query endpoints return accurate counts and summaries.
- Data Models (high-level):
  ```python
  sampling_summary = {"total": int, "kept": int, "sampled": bool}
  stats = {"input_count": int, "output_count": int, "rejection_rate": float, "rejection_reasons": dict[str,int]}
  ```
- Business Rules:
  1. Stats computed on full set before sampling.
  2. Sampling keeps all accepted; rejected sampled per reason with hard cap.
  3. SDK must not block pipeline; drops must be observable.

## 8. Non-Functional Requirements
- Performance: +<3% step latency at 5k candidates; API P95 < 300ms for ingest without evidence.
- Scalability: Handle 50k decisions/step with sampling; bounded memory via streaming/reservoir.
- Availability: SDK continues when API down; data may be dropped but pipeline runs.
- Security: N/A (single-tenant assumption for v1).

## 9. AI Specific Section 🤖
- Models Used: Optional LLMs in pipelines; SDK/API treat evidence as opaque blobs.
- Constraints: Evidence payloads size-limited; recommend gzip.
- Fallback: If LLM evidence too large, store summary and mark truncated.

## 10. Data
- Storage: PostgreSQL/SQLite JSON for stats; evidence JSON with optional gzip flag.
- Retention: Configurable TTL per pipeline for evidence; decisions optional TTL in future.

## 11. Assumptions and Dependencies
- Single-tenant, trusted environment.
- Python SDK only.
- Postgres recommended for JSONB indexing.

## 12. Open Questions
- [ ] Exact default hard cap for sampled decisions? (e.g., 1,000)
- [ ] Default buffer size/flush interval for SDK?
- [ ] Should SDK always send raw counts even when pre-sampling? (proposed yes)

## 13. Out of Scope (Explicit)
- Multi-language SDKs, auth/z, tenant isolation, full UI redesign.

## 14. Versioning and Change Log
**Version**: v1.0  
**Date**: 2026-01-13  
**Changes**: Initial PRD for lightweight capture and sampling.
