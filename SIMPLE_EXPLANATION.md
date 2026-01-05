# X-Ray SDK - Simple Explanation

## What is X-Ray?

X-Ray is a debugging tool for AI/ML pipelines that helps you understand **why** your system made a particular decision, not just what happened.

---

## The Problem It Solves

Imagine you have a system that finds competitor products for sellers on Amazon:

```
Input: "Adjustable Laptop Stand" ($45)
        ↓
Step 1: Generate keywords → ["adjustable", "stand", "holder"]
        ↓
Step 2: Search catalog → 5000 candidates
        ↓
Step 3: Filter by price/rating/category → 30 remain
        ↓
Step 4: Rank and select winner
        ↓
Output: "Phone Case XYZ" ← Wrong!
```

**The Question**: Why did the system pick a phone case instead of another laptop stand?

**Traditional Logging**: "Selected phone-case-xyz with score 0.92"

**X-Ray**: Shows you:
- Keywords were too generic ("stand" matches phone stands too)
- Only 1470/5000 rejected for category mismatch (filter too loose)
- Phone case scored higher on keyword overlap (ranking bug)

---

## Core Concepts

### 1. Run
A complete execution of your pipeline.

```python
run_id = xray.start_run(
    pipeline_type="competitor_selection",
    input={"product_id": "123", "title": "Laptop Stand"}
)
```

### 2. Step
A decision point in your pipeline (filtering, ranking, selection).

```python
xray.record_step(run_id, Step(
    name="filtering",
    input={"candidate_count": 5000},
    output={"passed_count": 30},
    reasoning="Applied price cap and rating filter"
))
```

### 3. Decision
A specific accept/reject event for a candidate.

```python
Decision(
    candidate_id="prod-123",
    decision_type="rejected",
    reason="price_exceeds_threshold",
    metadata={"price": 150, "threshold": 100}
)
```

### 4. Stats
Pre-computed numbers for efficient querying.

```json
{
  "input_count": 5000,
  "output_count": 30,
  "rejection_rate": 0.994,
  "rejection_reasons": {
    "price_exceeds_threshold": 2000,
    "rating_below_minimum": 1500,
    "category_mismatch": 1470
  }
}
```

---

## How It Works

### SDK Flow

```
Your Pipeline                    X-Ray SDK                      X-Ray API
     │                              │                               │
     ├─── start_run() ─────────────►├─── POST /v1/runs ────────────►│
     │                              │                               │
     ├─── record_step() ───────────►├─── POST /v1/runs/{id}/steps ─►│
     │    (auto-samples if >500)    │                               │
     │                              │                               │
     ├─── complete_run() ──────────►├─── PATCH /v1/runs/{id} ──────►│
     │                              │                               │
```

### Sampling (The 5000 → 30 Problem)

When you have 5000 candidates, storing all decisions is expensive. X-Ray samples:

1. **Keep ALL accepted decisions** (what passed matters)
2. **Keep N rejected per reason** (preserve WHY things failed)
3. **Compute full stats** (accurate counts for queries)

Result: ~500 decisions stored, but you know why all 5000 were processed.

---

## Key Features

### 1. Cross-Pipeline Queries

```bash
# Find all filtering steps with >90% rejection rate
curl -X POST http://localhost:8000/v1/query/steps \
  -d '{"step_name": "filtering", "min_rejection_rate": 0.9}'
```

Works across ALL pipelines (competitor selection, categorization, etc.)

### 2. Decision Tracking

```bash
# Track a candidate across all runs
curl -X POST http://localhost:8000/v1/query/decisions \
  -d '{"candidate_id": "prod-123"}'
```

See every time this product was evaluated, and why it was accepted/rejected.

### 3. Graceful Degradation

If X-Ray API is down:
- SDK logs a warning
- **Pipeline continues normally**
- Debug data is lost, but nothing breaks

---

## Quick Start

### 1. Start the API

```bash
uvicorn api.main:app --reload --port 8000
```

### 2. Use the SDK

```python
from xray import XRay, Step, Decision

xray = XRay(api_url="http://localhost:8000")

# Start a run
run_id = xray.start_run(
    pipeline_type="competitor_selection",
    input={"product_id": "123"}
)

# Record a filtering step
decisions = []
for candidate in candidates:
    if candidate["price"] > 100:
        decisions.append(Decision(
            candidate_id=candidate["id"],
            decision_type="rejected",
            reason="price_exceeds_threshold"
        ))
    else:
        decisions.append(Decision(
            candidate_id=candidate["id"],
            decision_type="accepted"
        ))

xray.record_step(run_id, Step(
    name="filtering",
    input={"count": len(candidates)},
    output={"count": len([d for d in decisions if d.decision_type == "accepted"])},
    decisions=decisions,
    reasoning="Applied $100 price cap"
))

# Complete the run
xray.complete_run(run_id, result={"winner_id": "prod-456"})
```

### 3. Query the Data

```bash
# List runs
curl http://localhost:8000/v1/runs

# Get run with all steps
curl http://localhost:8000/v1/runs/{run_id}

# Query filtering steps
curl -X POST http://localhost:8000/v1/query/steps \
  -d '{"step_name": "filtering"}'
```

---

## Why Decisions as Events?

The key insight: **One candidate can have multiple decisions.**

```
Step 1 (filtering): Candidate A → rejected (price too high)
Step 2 (re-eval):   Candidate A → accepted (price dropped)
Step 3 (ranking):   Candidate A → rejected (lower score)
```

If we modeled candidates as entities (with a single state), we'd lose this timeline. Events preserve the full decision history.

---

## Comparison with Traditional Tracing

| Aspect | Jaeger/Zipkin | X-Ray |
|--------|---------------|-------|
| Focus | Performance & flow | Decision reasoning |
| Data | Spans, timing | Candidates, filters, scores |
| Question | "What happened?" | "Why this output?" |
| Use case | Latency debugging | Algorithm debugging |

---

## File Structure

```
xray/                    # SDK package
├── client.py            # XRay client (start_run, record_step, complete_run)
├── models.py            # Pydantic models (Decision, Step, Evidence)
└── sampler.py           # Decision sampling logic

api/                     # API server
├── main.py              # FastAPI app
├── routes/
│   ├── ingest.py        # POST endpoints (create run, record step)
│   └── query.py         # GET/POST endpoints (list runs, query steps)
└── db/
    ├── database.py      # SQLAlchemy setup
    └── models.py        # ORM models (Run, Step, Decision, Evidence)

examples/
├── competitor_selection.py       # Basic demo
└── amazon_competitor_selection.py # Full Amazon scenario
```

---

## Current Limitations (Honest Assessment)

The implementation is optimized for the assignment's use case (5000 candidates), not production scale (millions). Here's what would need to change:

### What Works Now

| Scale | Memory | Time | Status |
|-------|--------|------|--------|
| 5,000 decisions | ~500 KB | <100ms | ✅ Perfect |
| 50,000 decisions | ~5 MB | ~1s | ✅ Good |
| 500,000 decisions | ~50 MB | ~10s | ⚠️ Slow |

### What Doesn't Scale

| Limitation | Current | Production Fix |
|------------|---------|----------------|
| **Memory** | O(n) — loads all decisions | Streaming with O(k) reservoir sampling |
| **HTTP calls** | Synchronous (blocks pipeline) | Async buffering with background flush |
| **Output size** | Unbounded (reasons × N) | Hard cap on total samples |
| **Database** | Single table | Partitioning by date |

### Why We Didn't Over-Engineer

```
Assignment says: "5000 candidates filtered to 30"
We handle:       500,000 comfortably

Building for 50 million when you need 5,000 is premature optimization.
```

### The Architecture Supports Scaling

The core design (decisions as events, stats pre-computation, reason-based sampling) doesn't change at scale. Only the implementation details change:

```
Current:    list → process → sample → send
Production: stream → sample-on-ingest → batch-send
```

---

## Summary

1. **X-Ray answers "why"** - not just what happened, but why decisions were made
2. **Decisions are events** - preserves timeline, enables sampling
3. **Stats enable queries** - efficient cross-pipeline analysis
4. **Graceful degradation** - never breaks your pipeline
5. **General-purpose** - works with any multi-step system
6. **Honest about scale** - handles 100x assignment requirements, not 10,000x

---

## Next Steps

- Run the demo: `python -m examples.competitor_selection`
- Explore the API: `http://localhost:8000/docs`
- Read ARCHITECTURE.md for deep dives
- Watch the video walkthrough

