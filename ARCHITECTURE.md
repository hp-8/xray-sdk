# X-Ray Architecture

## What Is This?

X-Ray is a debugging tool for non-deterministic pipelines. Traditional tracing answers "what happened?" - X-Ray answers "**why did the system make this decision?**"

```
┌─────────────────────────────────────────────────────────────┐
│                      X-Ray System                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌─────────────────┐     ┌──────────────────────────────┐ │
│   │   X-Ray SDK     │     │        X-Ray API             │ │
│   │                 │     │                              │ │
│   │ • XRay Client   │────▶│ • Ingest Endpoints           │ │
│   │ • Decision      │     │   POST /v1/runs              │ │
│   │ • Step          │     │   POST /v1/runs/{id}/steps   │ │
│   │ • Sampler       │     │                              │ │
│   │                 │     │ • Query Endpoints            │ │
│   │                 │◀────│   GET /v1/runs               │ │
│   │                 │     │   POST /v1/query/steps       │ │
│   └─────────────────┘     │   POST /v1/query/decisions   │ │
│                           └──────────────────────────────┘ │
│                                        │                    │
│                                        ▼                    │
│                           ┌──────────────────────────────┐ │
│                           │   PostgreSQL (recommended)   │ │
│                           │   SQLite (default dev)       │ │
│                           │                              │ │
│                           │ • runs                       │ │
│                           │ • steps (with stats)         │ │
│                           │ • decisions (primary events) │ │
│                           │ • evidence                   │ │
│                           └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Model

### Hierarchy

```
Run (pipeline execution)
 └── Step (decision point)
      └── Decision (time-ordered event)
           └── Evidence (extra context)
```

### Tables

> DB note: Default local dev uses SQLite (`DATABASE_URL=sqlite+aiosqlite:///./xray.db`). For production, set `DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DBNAME` and install `asyncpg`. No code changes required; the SQLAlchemy URL switches engines automatically.

| Table | Key Fields | What it stores |
|-------|------------|----------------|
| **runs** | `id`, `pipeline_type`, `status`, `input_context`, `output_result` | Complete pipeline executions |
| **steps** | `id`, `run_id`, `step_name`, `stats`, `reasoning` | Decision points with pre-computed stats |
| **decisions** | `id`, `step_id`, `candidate_id`, `decision_type`, `reason`, `score` | Individual decision events |
| **evidence** | `id`, `decision_id`, `evidence_type`, `data` | Heavy stuff (LLM outputs, API responses) |

### Why Decisions as Events?

The core insight: **one candidate can have multiple decisions across steps.**

Consider this timeline:
- Step 1 (filtering): Candidate A **rejected** - price too high
- Step 2 (re-evaluation): Candidate A **accepted** - reconsidered with discount
- Step 3 (ranking): Candidate A **rejected** - lower score than winner

If we modeled candidates as entities with a single status, we'd lose this history. Decisions as events preserve the full timeline.

This also makes sampling work better. When you have 5000 candidates, sampling *decision events* (all accepted + N rejected per reason) keeps more useful debugging info than sampling entities.

### Data Structure Choices

| Structure | Choice | Why |
|-----------|--------|-----|
| **Decisions** | `list[Decision]` | Preserves ordering; same candidate can appear multiple times |
| **Stats** | `dict` (JSON) | Different steps have different stats |
| **Input/Output** | `dict` (JSON) | Domain-agnostic |
| **Grouping by reason** | `dict[str, list]` | O(1) lookup for "N per reason" sampling |
| **Decisions storage** | Separate SQL table | Enables queries like "all decisions for candidate X" |

---

## API

### Creating Data

**Start a run:**
```http
POST /v1/runs
{
  "pipeline_type": "competitor_selection",
  "name": "find_competitor_product-123",
  "input": {"product_id": "product-123", "title": "Laptop Stand"},
  "metadata": {"source": "api"}
}
→ {"run_id": "uuid-here"}
```

**Record a step:**
```http
POST /v1/runs/{run_id}/steps
{
  "name": "filtering",
  "input": {"candidate_count": 5000},
  "output": {"passed_count": 30},
  "config": {"price_threshold": 100, "min_rating": 3.5},
  "decisions": [
    {
      "candidate_id": "prod-123",
      "decision_type": "rejected",
      "reason": "price_exceeds_threshold",
      "metadata": {"price": 150, "threshold": 100}
    }
  ],
  "reasoning": "Applied price cap ($100) and minimum rating (3.5)"
}
→ {"step_id": "uuid", "stats": {...}}
```

**Complete a run:**
```http
PATCH /v1/runs/{run_id}
{
  "result": {"competitor_id": "prod-456"},
  "status": "completed"
}
```

### Querying Data

```http
GET /v1/runs?pipeline_type=competitor_selection&status=completed&page=1&page_size=20
GET /v1/runs/{run_id}?include_decisions=true

POST /v1/query/steps
{"step_name": "filtering", "min_rejection_rate": 0.9}

POST /v1/query/decisions
{"candidate_id": "prod-123"}
```

---

## Debugging Example

**Problem:** competitor selection returned a phone case for a laptop stand.

### Step 1: Find the run

```python
runs = xray.query_runs(pipeline_type="competitor_selection")
# find the bad one
```

### Step 2: Inspect it

```python
run = xray.get_run(run_id, include_decisions=True)
# input: {"title": "Adjustable Laptop Stand"}
# output: {"competitor_id": "phone-case-xyz"}
# steps: keyword_generation → candidate_search → filtering → final_selection
```

### Step 3: Check each step

**Keyword generation:**
```json
{
  "input": {"title": "Adjustable Laptop Stand"},
  "output": {"keywords": ["adjustable", "stand", "holder", "desk accessory"]},
  "reasoning": "Extracted keywords from title"
}
```
Problem: keywords are too generic. "stand" and "holder" match phone stuff.

**Filtering stats:**
```json
{
  "stats": {
    "input_count": 5000,
    "output_count": 30,
    "rejection_rate": 0.994,
    "rejection_reasons": {
      "price_exceeds_threshold": 2000,
      "rating_below_minimum": 1500,
      "category_mismatch": 1470
    }
  }
}
```
Problem: only 1470 rejected for category mismatch. Many phone accessories slipped through.

**Final selection:**
```json
{
  "decisions": [
    {"candidate_id": "phone-case-xyz", "decision_type": "accepted", "score": 0.92},
    {"candidate_id": "laptop-stand-abc", "decision_type": "rejected", "score": 0.87}
  ]
}
```
Problem: phone case scored higher on keyword overlap.

### Root cause

1. Keywords too generic
2. Category filter too loose
3. Ranking over-weighted keyword overlap

### Fix

1. Category-aware keyword extraction
2. Stricter category matching
3. Weight category match higher in ranking

---

## Cross-Pipeline Queries

### The Problem

Multiple pipelines (competitor selection, listing optimization, categorization...) with different steps. Users want to ask:

> "Show me all runs where filtering eliminated more than 90% of candidates"

...regardless of which pipeline.

### The Solution

**1. Consistent naming conventions:**

| Pattern | Examples |
|---------|----------|
| `*_generation` | `keyword_generation`, `draft_generation` |
| `*_search` | `candidate_search`, `product_search` |
| `filtering` | `filtering`, `price_filtering` |
| `*_selection` | `final_selection`, `winner_selection` |

**2. Pre-computed stats on every step:**

```json
{
  "stats": {
    "input_count": 5000,
    "output_count": 30,
    "rejection_rate": 0.994,
    "rejection_reasons": {"price_exceeds_threshold": 2000, ...}
  }
}
```

Query without scanning all decisions:
```sql
SELECT * FROM steps 
WHERE step_name = 'filtering' 
AND (stats->>'rejection_rate')::float > 0.9;
```

**3. Cross-pipeline query endpoint:**

```http
POST /v1/query/steps
{
  "step_name": "filtering",
  "min_rejection_rate": 0.9,
  "pipeline_type": null  // all pipelines
}
```

**4. Decision-level queries:**

Track a candidate across runs:
```http
POST /v1/query/decisions
{"candidate_id": "prod-123"}
```

Find all rejections for a reason:
```http
POST /v1/query/decisions
{"reason": "category_mismatch", "step_name": "filtering"}
```

### Developer Constraints

To make this work, devs need to:
1. Use consistent step names
2. Use standardized reason strings (`price_exceeds_threshold`, not `"price was too high"`)
3. Include `candidate_id` on all decisions
4. Record input/output counts

---

## Handling Scale

### The 5000 → 30 Problem

A filtering step evaluates 5000 candidates and passes 30. What do we store?

**Answer:** sample the decisions.

```
Sampler config:
  threshold: 500        # when to start sampling
  per_reason: 50        # rejected decisions to keep per reason

Algorithm:
  1. Keep ALL accepted (we care about what passed)
  2. Keep N random rejected PER REASON (preserve diversity)
  3. Maintain time-ordering
```

**Trade-offs:**

| Approach | Completeness | Storage | Query Speed |
|----------|--------------|---------|-------------|
| Store all 5000 | 100% | High | Slow |
| Sample to 500 | ~10% rejected, 100% accepted | Low | Fast |
| Stats only | 0% detail | Minimal | Fastest |

We chose sampling + pre-computed stats. You get:
- Full detail on accepted decisions (debug winners)
- Representative rejected decisions (understand failures)
- Efficient queries via stats

---

## Developer Experience

### Minimal

```python
from xray import XRay, Step

xray = XRay()
run_id = xray.start_run(pipeline_type="my_pipeline")
xray.record_step(run_id, Step(name="step1"))
xray.complete_run(run_id)
```

### Full

```python
from xray import XRay, Step, Decision

xray = XRay()
run_id = xray.start_run(
    pipeline_type="competitor_selection",
    input={"product_id": "123"}
)

decisions = [
    Decision(
        candidate_id=c["id"],
        decision_type="rejected",
        reason="price_too_high",
        metadata={"price": c["price"]}
    )
    for c in rejected_candidates
]

xray.record_step(run_id, Step(
    name="filtering",
    input={"count": 5000},
    output={"count": 30},
    decisions=decisions,
    reasoning="Applied price filter"
))

xray.complete_run(run_id, result={"winner": "product-456"})
```

### When the API is down

1. SDK logs a warning
2. Pipeline continues (non-blocking)
3. Debug data lost for that run

The debug system should never break the pipeline it's debugging.

---

## Real-World: Content Generation Pipeline

During early work on **Socella** (AI-powered social content workspace), I was designing a content generation pipeline. The system would:

- Interpret user intent and brand context
- Generate multiple drafts via LLM
- Apply filters (tone, length, platform constraints)
- Score and rank drafts
- Select a final version

Even at the design stage, questions came up:
- Which drafts were generated but discarded?
- Why was one version selected over others?
- Is this a prompt issue, filter issue, or ranking issue?

Answering these would require ad-hoc logging and manual inspection.

### How X-Ray Would Help

```python
from xray import XRay, Step, Decision, Evidence

xray = XRay()
run_id = xray.start_run(
    pipeline_type="content_generation",
    input={"user_intent": "announce product launch", "brand_voice": "professional"}
)

# Generate drafts
drafts = llm.generate_multiple_drafts(prompt, count=10)
xray.record_step(run_id, Step(
    name="draft_generation",
    input={"prompt": prompt, "count": 10},
    output={"drafts_generated": len(drafts)},
    evidence=[Evidence(evidence_type="llm_output", data=d) for d in drafts],
    reasoning="Generated 10 draft variations using GPT-4"
))

# Filter
decisions = []
for draft in drafts:
    tone_ok = check_tone(draft, brand_voice)
    length_ok = check_length(draft, platform="twitter")
    
    if not tone_ok or not length_ok:
        decisions.append(Decision(
            candidate_id=draft.id,
            decision_type="rejected",
            reason="tone_mismatch" if not tone_ok else "length_exceeded",
            metadata={"tone_score": tone_ok, "length": len(draft)}
        ))
    else:
        decisions.append(Decision(
            candidate_id=draft.id,
            decision_type="accepted",
            score=calculate_relevance(draft)
        ))

xray.record_step(run_id, Step(
    name="filtering",
    input={"draft_count": len(drafts)},
    output={"passed_count": sum(1 for d in decisions if d.decision_type == "accepted")},
    decisions=decisions,
    reasoning="Applied tone and length filters"
))

# Select winner
ranked = rank_drafts([d for d in drafts if passed_filter(d)])
winner = ranked[0]
xray.complete_run(run_id, result={"selected_draft_id": winner.id})
```

**Debugging scenarios:**

1. **"Why did we reject 8/10 drafts?"** — Query filtering steps with high rejection rate, inspect reasons
2. **"Why draft X over draft Y?"** — Compare scores and ranking logic
3. **"Are prompts generating off-brand content?"** — Query decisions with `reason="tone_mismatch"`, review LLM evidence
4. **"Which filter kills the best drafts?"** — Cross-run analysis of high-scoring rejections

---

## Future Work

If productionizing:

1. **Observability**: SDK metrics, API dashboards
2. **Storage**: TTL, tiered storage, compression
3. **Security**: Auth, tenant isolation, PII redaction
4. **DX**: Web UI, CLI, VS Code extension
5. **Querying**: Full-text search on reasoning, anomaly detection
6. **SDK**: Multi-language (JS, Go), OpenTelemetry integration

---

## vs Traditional Tracing

| | Jaeger/Zipkin | X-Ray |
|--|---------------|-------|
| Focus | Performance & request flow | Decision reasoning |
| Data | Spans, timing, service calls | Decisions, candidates, filters |
| Question | "What happened?" | "Why this output?" |
| Granularity | Function/service level | Business logic level |
| Use case | Latency debugging | Algorithm debugging |
