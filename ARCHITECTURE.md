# X-Ray SDK & API - Architecture Document

## Overview

X-Ray is a debugging system for non-deterministic, multi-step algorithmic pipelines. Unlike traditional tracing which answers "what happened?", X-Ray answers "**why did the system make this decision?**"

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
│                           │       PostgreSQL             │ │
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

### Entity Relationship

```
Run (pipeline execution)
 └── Step (decision point)
      └── Decision (time-ordered event)
           └── Evidence (additional context)
```

### Schema

| Table | Key Fields | Purpose |
|-------|------------|---------|
| **runs** | `id`, `pipeline_type`, `status`, `input_context`, `output_result` | Track complete pipeline executions |
| **steps** | `id`, `run_id`, `step_name`, `stats`, `reasoning` | Capture decision points with pre-computed stats |
| **decisions** | `id`, `step_id`, `candidate_id`, `decision_type`, `reason`, `score` | Time-ordered decision events |
| **evidence** | `id`, `decision_id`, `evidence_type`, `data` | Heavy context (LLM outputs, API responses) |

### Data Model Rationale

**Why Decisions as Primary Events (not Candidates as Entities)?**

1. **One candidate, multiple decisions**: A candidate can be evaluated multiple times across steps. With decisions as events, we capture the full timeline:
   - Step 1 (filtering): Candidate A **rejected** (price too high)
   - Step 2 (re-evaluation): Candidate A **accepted** (reconsidered)
   - Step 3 (ranking): Candidate A **rejected** (lower score than winner)

2. **Sampling preserves reasoning**: When handling 5000 candidates, sampling decision events (all accepted + N rejected per reason) preserves reasoning diversity better than sampling entities.

3. **Time-ordering is natural**: Decisions have timestamps and sequence order. Debugging often requires understanding "when did this decision happen relative to others?"

**What would break with a different model?**

- **Candidates as JSONB on Step**: Loses ability to query "all decisions for candidate X across all runs"
- **Candidates as separate entity table**: Awkward to model multiple decisions for the same candidate

### Why These Data Structures?

| Structure | Choice | Reason |
|-----------|--------|--------|
| **Decisions** | `list[Decision]` (array) | Preserves time-ordering; same candidate can have multiple decisions across the pipeline |
| **Stats** | `dict` (JSON) | Flexible schema; different steps have different stats |
| **Input/Output** | `dict` (JSON) | Domain-agnostic; competitor selection has different inputs than content generation |
| **Grouping by reason** | `dict[str, list]` | O(1) lookup for "N per reason" sampling vs O(n×k) for repeated filtering |
| **Decisions storage** | Separate SQL table | Enables cross-run queries: "all decisions for candidate X" |

**Key principle**: Optimize for debugging queries, not just storage efficiency.

---

## API Specification

### Ingest Endpoints

#### Create Run
```
POST /v1/runs
Content-Type: application/json

{
  "pipeline_type": "competitor_selection",
  "name": "find_competitor_product-123",
  "input": {"product_id": "product-123", "title": "Laptop Stand"},
  "metadata": {"source": "api"}
}

Response: {"run_id": "uuid-here"}
```

#### Record Step
```
POST /v1/runs/{run_id}/steps
Content-Type: application/json

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

Response: {"step_id": "uuid", "stats": {...}}
```

#### Complete Run
```
PATCH /v1/runs/{run_id}
Content-Type: application/json

{
  "result": {"competitor_id": "prod-456"},
  "status": "completed"
}
```

### Query Endpoints

#### List Runs
```
GET /v1/runs?pipeline_type=competitor_selection&status=completed&page=1&page_size=20
```

#### Get Run with Steps
```
GET /v1/runs/{run_id}?include_decisions=true
```

#### Query Steps (Cross-Pipeline)
```
POST /v1/query/steps
{
  "step_name": "filtering",
  "min_rejection_rate": 0.9
}
```

#### Query Decisions
```
POST /v1/query/decisions
{
  "candidate_id": "prod-123"
}
```

---

## Debugging Walkthrough: Phone Case vs Laptop Stand

**Scenario**: A competitor selection run returns a poor match—a **phone case** matched against a **laptop stand**.

### Step 1: Find the Run

```python
runs = xray.query_runs(pipeline_type="competitor_selection")
# Find the problematic run by inspecting outputs
```

### Step 2: Inspect the Run

```python
run = xray.get_run(run_id, include_decisions=True)
# Shows:
# - input: {"title": "Adjustable Laptop Stand"}
# - output: {"competitor_id": "phone-case-xyz"}
# - steps: ["keyword_generation", "candidate_search", "filtering", "final_selection"]
```

### Step 3: Check Each Step

**Keyword Generation**:
```json
{
  "input": {"title": "Adjustable Laptop Stand"},
  "output": {"keywords": ["adjustable", "stand", "holder", "desk accessory"]},
  "reasoning": "Extracted keywords from title"
}
```
**Issue**: Keywords are too generic. "stand" and "holder" match phone accessories.

**Filtering Stats**:
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
**Issue**: Only 1470 rejected for category mismatch. Many phone accessories passed.

**Final Selection**:
```json
{
  "decisions": [
    {"candidate_id": "phone-case-xyz", "decision_type": "accepted", "score": 0.92},
    {"candidate_id": "laptop-stand-abc", "decision_type": "rejected", "score": 0.87}
  ],
  "reasoning": "Selected based on highest keyword overlap"
}
```
**Issue**: Phone case scored higher on keyword overlap.

### Root Cause

1. **Keywords too generic**: "stand", "holder" match phone accessories
2. **Category filter too loose**: Allowed cross-category matches
3. **Ranking over-weighted keywords**: Didn't penalize category mismatch enough

### Fix

1. Add category-aware keyword extraction
2. Stricter category matching in filters
3. Weight category match higher in ranking

---

## Queryability: Cross-Pipeline Analysis

### The Challenge

The system will be used across multiple pipelines (competitor selection, listing optimization, categorization, etc.), each with different steps. Users need to ask questions like:

> "Show me all runs where the filtering step eliminated more than 90% of candidates"—regardless of which pipeline it was.

### Our Solution

#### 1. Consistent Step Naming Conventions

Developers follow naming conventions for common step types:

| Convention | Examples | Query Use |
|------------|----------|-----------|
| `*_generation` | `keyword_generation`, `draft_generation` | Find all generation steps |
| `*_search` | `candidate_search`, `product_search` | Find search bottlenecks |
| `filtering` | `filtering`, `price_filtering` | Analyze filter effectiveness |
| `ranking` | `ranking`, `relevance_ranking` | Debug ranking logic |
| `*_selection` | `final_selection`, `winner_selection` | Trace final decisions |

#### 2. Pre-computed Stats on Steps

Every step stores pre-computed statistics:

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

This enables efficient queries without scanning decision tables:

```sql
SELECT * FROM steps 
WHERE step_name = 'filtering' 
AND (stats->>'rejection_rate')::float > 0.9;
```

#### 3. Cross-Pipeline Query API

```
POST /v1/query/steps
{
  "step_name": "filtering",
  "min_rejection_rate": 0.9,
  "pipeline_type": null  // All pipelines
}
```

#### 4. Decision-Level Queries

Track a candidate across all runs:

```
POST /v1/query/decisions
{
  "candidate_id": "prod-123"
}
```

Find all rejections for a specific reason:

```
POST /v1/query/decisions
{
  "reason": "category_mismatch",
  "step_name": "filtering"
}
```

### Constraints on Developers

To enable queryability, developers must:

1. **Use consistent step names** across similar pipelines
2. **Provide decision reasons** as standardized strings (e.g., `price_exceeds_threshold`, not `"price was too high"`)
3. **Include candidate_id** for all decisions (enables cross-run tracking)
4. **Record stats-relevant data**: input counts, output counts for filtering steps

### Extensibility for New Use Cases

The system is extensible via:

- **`pipeline_type`**: Group runs by pipeline
- **`metadata`** fields: Add custom queryable attributes
- **`step.config`**: Store filter configurations for analysis

---

## Performance & Scale

### The 5000 → 30 Problem

**Question**: How do you handle a step that evaluates 5000 candidates and passes 30?

**Answer**: Decision sampling in the SDK.

```python
# SDK Sampling Strategy
Sampler:
  threshold: 500        # Max decisions before sampling
  per_reason: 50        # Rejected decisions to keep per reason

Algorithm:
  1. Keep ALL accepted decisions (we care about what passed)
  2. Keep N random rejected decisions PER REASON (preserve diversity)
  3. Maintain time-ordering (sequence_order field)
```

**Trade-offs**:

| Approach | Completeness | Storage | Query Speed |
|----------|--------------|---------|-------------|
| Store all 5000 | 100% | High | Slow |
| Sample to 500 | ~10% rejected, 100% accepted | Low | Fast |
| Stats only | 0% detail | Minimal | Fastest |

**Our choice**: Sample decisions + pre-compute stats. This gives:
- Full detail on accepted decisions (debugging winners)
- Representative rejected decisions (understanding why things failed)
- Efficient queries via stats (rejection_rate > 0.9)

**Who decides?** The developer controls sampling threshold via SDK config. System always computes stats for queryability.

---

## Developer Experience

### Minimal Instrumentation

```python
from xray import XRay, Step

xray = XRay()
run_id = xray.start_run(pipeline_type="my_pipeline")
xray.record_step(run_id, Step(name="step1"))
xray.complete_run(run_id)
```

### Full Instrumentation

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

### Backend Unavailability

If the X-Ray API is down:
1. SDK logs a warning
2. Pipeline continues execution (non-blocking)
3. Debug data is lost for that run

**Rationale**: Debug system should never break the pipeline it's debugging.

---

## Real-World Application

### Socella: AI-Powered Social Content Generation Pipeline

During the early stages of **Socella**, an AI-powered workspace for managing social content, I was involved in designing a content generation pipeline, although it was not fully implemented due to the product being in an early validation phase.

The proposed system was intended to:
- Interpret user intent and brand context
- Generate multiple content drafts using an LLM
- Apply heuristic filters such as tone, length, and platform constraints
- Score and rank drafts based on engagement and relevance signals
- Select a final version for publishing or review

While working through this design, it became clear that the pipeline would be inherently non-deterministic. The same input could yield different outputs across runs, which was desirable for creativity but posed a major challenge for debugging and iteration.

Even at the design stage, several debugging questions emerged:
- How would we know which drafts were generated but discarded?
- How would we explain why one version was selected over others?
- How would we distinguish prompt issues from filtering or ranking issues?
- How could we systematically improve the system without replaying entire runs?

Answering these questions would have required significant ad-hoc logging and manual inspection, making iteration slow and fragile.

### Retrofitting X-Ray into Socella

X-Ray is directly informed by this experience. It formalizes the debugging needs identified during the design phase by:
- **Modeling each draft evaluation as a decision event**: Each generated draft becomes a candidate with an explicit accept/reject decision
- **Capturing filtering and ranking outcomes explicitly**: Filter steps record which drafts failed tone checks, length constraints, or platform rules
- **Storing LLM prompts and outputs as evidence**: The original prompt, generated drafts, and LLM reasoning are stored as evidence attached to decisions
- **Enabling cross-run analysis of failure patterns**: Query "all runs where tone filter rejected >50% of drafts" to identify systematic issues

**Integration Example:**

```python
from xray import XRay, Step, Decision, Evidence

xray = XRay()
run_id = xray.start_run(
    pipeline_type="content_generation",
    input={"user_intent": "announce product launch", "brand_voice": "professional"}
)

# Step 1: Generate drafts
drafts = llm.generate_multiple_drafts(prompt, count=10)
xray.record_step(run_id, Step(
    name="draft_generation",
    input={"prompt": prompt, "count": 10},
    output={"drafts_generated": len(drafts)},
    evidence=[Evidence(evidence_type="llm_output", data=d) for d in drafts],
    reasoning="Generated 10 draft variations using GPT-4"
))

# Step 2: Apply filters
decisions = []
for draft in drafts:
    tone_check = check_tone(draft, brand_voice)
    length_check = check_length(draft, platform="twitter")
    
    if not tone_check or not length_check:
        decisions.append(Decision(
            candidate_id=draft.id,
            decision_type="rejected",
            reason="tone_mismatch" if not tone_check else "length_exceeded",
            metadata={"tone_score": tone_check, "length": len(draft)}
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

# Step 3: Ranking and selection
ranked = rank_drafts([d for d in drafts if passed_filter(d)])
winner = ranked[0]

xray.complete_run(run_id, result={"selected_draft_id": winner.id})
```

**Debugging Scenarios X-Ray Would Enable:**

1. **"Why did we reject 8 out of 10 drafts?"**
   - Query: `POST /v1/query/steps` with `step_name="filtering"` and `min_rejection_rate=0.8`
   - Inspect rejection reasons: Are tone filters too strict? Length constraints too tight?

2. **"Why was draft X selected over draft Y?"**
   - Get run: `GET /v1/runs/{run_id}?include_decisions=true`
   - Compare scores and ranking logic for both drafts

3. **"Are our prompts generating off-brand content?"**
   - Query decisions: `POST /v1/query/decisions` with `reason="tone_mismatch"`
   - Review evidence (LLM outputs) to identify prompt issues

4. **"Which filter is eliminating the best drafts?"**
   - Cross-run analysis: Find runs where high-scoring drafts were rejected
   - Adjust filter thresholds based on data

Although the Socella pipeline was not fully implemented, the design process highlighted the exact class of problems X-Ray is meant to solve: **non-deterministic, multi-step systems where understanding decision reasoning is critical for iteration and improvement**.

---

## What Next: Future Improvements

If shipping for production:

1. **Observability**: SDK metrics (latency, error rates), API dashboards
2. **Storage**: TTL policies, tiered storage, compression
3. **Security**: API authentication, tenant isolation, PII redaction
4. **DX**: Web UI for exploration, CLI tool, VS Code extension
5. **Querying**: Full-text search on reasoning, anomaly detection
6. **SDK**: Multi-language support (JS, Go), OpenTelemetry integration

---

## Comparison with Traditional Tracing

| Aspect | Traditional Tracing (Jaeger, Zipkin) | X-Ray |
|--------|--------------------------------------|-------|
| Focus | Performance & request flow | Decision reasoning |
| Data | Spans, timing, service calls | Decisions, candidates, filters |
| Question | "What happened?" | "Why this output?" |
| Granularity | Function/service level | Business logic level |
| Use case | Latency debugging | Algorithm debugging |