# X-Ray SDK & API - Design Decisions & Rationale

This document captures the key design decisions made while building the X-Ray debugging system, along with the reasoning behind each choice. Use this to prepare for explaining the architecture and trade-offs.

---

## 1. SDK Design: Functional API vs Context Manager

### Decision: Functional API

We chose a **functional/record-based API** over a context manager approach.

### Why This Decision?

#### 1.1 General-Purpose Requirement

The assignment requires a **general-purpose** SDK that works across different pipeline architectures. Context managers assume that decision logic happens within a single code block:

```python
# Context Manager - assumes step = code block
with run.step("filtering") as step:
    for c in candidates:
        step.reject(c, reason="...")  # Must happen here
```

**Problem:** Real-world pipelines don't always fit this pattern:
- Decision logic might be in helper functions called from elsewhere
- LLM-based decisions might be made in separate services
- Parallel/async processing doesn't map cleanly to sequential blocks
- Class-based architectures with methods called from multiple places

**Solution:** Functional API decouples recording from code structure:

```python
# Decisions can be built anywhere, recorded when ready
decisions = build_decisions(candidates)  # In helper function
xray.record_step(run_id, Step(name="filtering", decisions=decisions))
```

This makes the SDK truly **domain-agnostic** and works with any code architecture.

#### 1.2 Decision Context Capture

The assignment emphasizes capturing **why** decisions were made, not just what happened. This requires:
- Full context before recording (to write meaningful reasoning)
- Ability to batch decision events
- Flexibility to add reasoning after understanding the complete picture

**Functional approach allows:**
```python
# Build decisions naturally during processing
decisions = []
for c in candidates:
    if c["price"] > 100:
        decisions.append(Decision(
            candidate_id=c["id"],
            decision_type="rejected",
            reason="price_exceeds_threshold",
            metadata={"price": c["price"], "threshold": 100}
        ))

# Add reasoning after you understand the full picture
xray.record_step(run_id, Step(
    name="filtering",
    decisions=decisions,
    reasoning=f"Applied price filter: {len(rejected)} rejected, {len(passed)} passed"
))
```

**Context manager forces inline recording**, which can fragment the decision context.

#### 1.3 Scale: The 5000 → 30 Problem

The assignment specifically asks: *"How does your system handle a step that takes 5,000 candidates and filters down to 30?"*

| Approach | How it handles scale |
|----------|---------------------|
| Context Manager | Must buffer internally as you call `step.reject()` 5000 times, then sample on exit |
| Functional | You build the list, SDK samples before sending - explicit control |

With functional, sampling strategy is explicit:
```python
xray.record_step(run_id, Step(
    name="filtering",
    decisions=decisions,  # SDK auto-samples if > threshold
))
```

The developer can see exactly what's being recorded, and the SDK handles sampling transparently.

#### 1.4 Minimal Instrumentation

The assignment asks: *"What's the minimal instrumentation to get something useful?"*

**Functional is less invasive:**
```python
# Existing code - ZERO changes to logic
def filter_candidates(candidates, config):
    return [c for c in candidates if c["price"] < config["max_price"]]

# Add X-Ray with minimal wrapping
def filter_candidates_with_xray(candidates, config, run_id, xray):
    result = filter_candidates(candidates, config)  # Original code untouched
    
    xray.record_step(run_id, Step(
        name="filtering",
        input={"count": len(candidates)},
        output={"count": len(result)},
        config=config
    ))
    return result
```

Context managers would require restructuring the original function to fit the `with` block pattern.

### Trade-offs

**What we gave up:**
- **Auto-timing**: Context managers automatically capture start/end timestamps
- **Exception safety**: Auto-cleanup on exceptions
- **Enforced structure**: Prevents forgetting to close steps

**Mitigation:** We can add a hybrid helper for developers who want this:
```python
with xray.traced_step(run_id, "filtering") as step:
    # Auto-timing, auto-complete on exit
    step.input = {...}
    step.output = {...}
```

---

## 2. Data Model: Decisions as Primary Events (Not Candidates)

### Decision: Decisions are the primary event, stored in a separate table

We model **Decisions** as first-class events, not just candidate states. The data model is:
- `Run` → `Step` → `Decision` → `Evidence`

### Options Considered

| Approach | Description | Verdict |
|----------|-------------|---------|
| **Candidates as JSONB** | Store candidate array on Step | Simple but loses timeline |
| **Candidates as Table** | Separate candidates table | Normalized but entity-focused |
| **Decisions as Events** | Each decision is a time-ordered event | **Chosen** - preserves reasoning |

### Why Decisions Over Candidates?

#### 2.1 One Candidate, Multiple Decisions

A candidate can be evaluated multiple times across steps:
- Step 1 (filtering): Candidate A **rejected** (price too high)
- Step 2 (re-evaluation): Candidate A **accepted** (price dropped, reconsidered)
- Step 3 (ranking): Candidate A **rejected** (lower score than winner)

With candidates-as-entities, this is awkward. With decisions-as-events, it's natural:

```python
decisions = [
    Decision(candidate_id="A", decision_type="rejected", reason="price_too_high", step="filtering"),
    Decision(candidate_id="A", decision_type="accepted", reason="price_dropped", step="re_evaluation"),
    Decision(candidate_id="A", decision_type="rejected", reason="lower_score", step="ranking"),
]
```

#### 2.2 Time-Ordered Reasoning

Decisions are naturally time-ordered. When debugging, you ask: *"What decisions were made about candidate A, and in what order?"*

With decisions as events, this is a simple query:
```sql
SELECT * FROM decisions 
WHERE candidate_id = 'A' 
ORDER BY created_at;
```

#### 2.3 Sampling Preserves Reasoning

The assignment asks about handling 5000 candidates → 30. Sampling entities loses context. Sampling **decision events** preserves reasoning diversity:

- Sample: **All accepted decisions** (we care about what passed)
- Sample: **N rejected decisions per reason** (preserve why things failed)
- Preserve: **Time ordering** (sequence_order field)

This is the key insight: **sampling decisions preserves reasoning better than sampling entities**.

#### 2.4 Evidence Separation

Decisions can have attached evidence (LLM outputs, API responses, computed scores). By separating Evidence from Decision, we:
- Keep Decision records lightweight
- Allow rich evidence when needed
- Query decisions without loading heavy evidence data

### Data Model

```
Run (pipeline execution)
  └── Step (decision point)
        └── Decision (time-ordered event)
              └── Evidence (optional context)
```

**Decision fields:**
- `candidate_id`: What was evaluated
- `decision_type`: accepted / rejected / pending
- `reason`: Why this decision
- `score`: Numeric score if applicable
- `sequence_order`: Order in which decision was made
- `metadata`: Additional context (JSONB)

### Trade-offs

**What we gave up:**
- **Simplicity**: More tables than JSONB approach
- **Atomic writes**: Need transactions for step + decisions

**Mitigation:**
- SDK handles batching and transactions
- Decisions table is append-only (fast inserts)
- Stats still pre-computed on Step for query efficiency

---

## 2.5. Data Structure Justifications

### Why `list[Decision]` (Array) for Decisions?

**Question you might get:** "Why use an array for decisions instead of a dictionary keyed by candidate_id?"

**Answer:**
```python
# Array preserves time-ordering and allows multiple decisions per candidate:
decisions = [
    Decision(candidate_id="A", decision_type="rejected", reason="price_too_high"),   # t=1
    Decision(candidate_id="B", decision_type="accepted"),                            # t=2
    Decision(candidate_id="A", decision_type="accepted", reason="reconsidered"),    # t=3
]

# Dictionary would lose this:
# decisions_dict = {"A": ???}  # Which decision? We lost the timeline.
```

| Property | Array | Dictionary |
|----------|-------|------------|
| Order preserved | ✅ Yes | ❌ No (Python 3.7+ preserves insertion order, but not semantic order) |
| Same candidate, multiple decisions | ✅ Yes | ❌ No (key collision) |
| Iteration for stats | ✅ O(n) single pass | ✅ O(n) |
| JSON serialization | ✅ Direct | ✅ Direct |

### Why `dict[str, list[Decision]]` for Grouping in Sampler?

```python
# In sampler.py:
rejected_by_reason: dict[str, list[Decision]] = defaultdict(list)
```

**Why not just filter the list each time?**

| Approach | Time Complexity |
|----------|-----------------|
| Build dict once, lookup per reason | O(n) build + O(1) per reason = **O(n)** |
| Filter list for each reason | O(n) per reason × k reasons = **O(n×k)** |

With 5000 decisions and 10 rejection reasons: **5000 vs 50,000 operations**.

### Why `dict[str, Any]` for Input/Output/Config?

**Question:** "Why not use typed Pydantic models for input/output?"

**Answer:** The SDK must be **domain-agnostic**. Different pipelines have different schemas:

```python
# Competitor selection:
input={"product_id": "123", "category": "electronics"}

# Content generation:
input={"prompt": "...", "tone": "professional", "platform": "twitter"}

# Categorization:
input={"text": "...", "taxonomy_version": "v2"}
```

A typed model would tie X-Ray to a specific domain. `dict[str, Any]` keeps it general-purpose.

### Why Separate Decisions Table (Not Embedded JSON)?

**Option A: Embed decisions in Step as JSON**
```sql
steps.decisions = '[{"candidate_id": "A", ...}, {...}]'
-- Can't query: "Find all decisions for candidate A across all runs"
```

**Option B: Separate table with foreign key**
```sql
SELECT * FROM decisions WHERE candidate_id = 'A';
-- Works across ALL steps and runs!
```

**Choice**: Separate table. Cross-run queries are a core requirement.

---

## 2.6. Sampling Algorithm: Deep Dive

### The Assignment Question

> "Consider a step that takes 5,000 candidates as input and filters down to 30. Capturing full details for all 5,000 (including rejection reasons) might be prohibitively expensive. How does your system handle this?"

### Our Algorithm

```python
# Strategy: Keep ALL accepted + N rejected PER REASON
if len(decisions) > threshold:  # Default: 500
    accepted = [d for d in decisions if d.decision_type == "accepted"]
    rejected_by_reason = group_by(decisions, key=lambda d: d.reason)
    
    sampled_rejected = []
    for reason, items in rejected_by_reason.items():
        sampled_rejected.extend(random.sample(items, min(N, len(items))))
    
    return accepted + sampled_rejected
```

### Why This Specific Strategy?

| Design Choice | Rationale |
|---------------|-----------|
| **Keep ALL accepted** | Winners matter most. "Why did this pass?" is the primary debugging question. |
| **N per rejection reason** | Preserves *why* things failed. 50 examples of "price_too_high" is enough to understand the pattern. |
| **Random within reason** | Simple, unbiased. No need to weight by score for most use cases. |
| **Stats computed BEFORE sampling** | Queries like "rejection_rate > 0.9" must reflect true rates, not sampled rates. |

### Limitations (Be Honest in Interview)

| Limitation | Impact | When It Matters |
|------------|--------|-----------------|
| **O(n) memory** | Must load all decisions before sampling | Scale > 500K decisions |
| **Random loses time order** | Can't answer "when did failures start?" | Debugging temporal patterns |
| **Unbounded output** | 20 reasons × 50 = 1000 (exceeds threshold) | Many diverse rejection reasons |
| **No importance weighting** | Borderline cases (score=0.49) treated same as clear rejections (score=0.1) | Debugging threshold tuning |

### Alternative Algorithms You Should Know

**Reservoir Sampling** (for streaming):
```python
# O(k) memory instead of O(n)
def reservoir_sample(stream, k):
    reservoir = []
    for i, item in enumerate(stream):
        if i < k:
            reservoir.append(item)
        else:
            j = random.randint(0, i)
            if j < k:
                reservoir[j] = item
    return reservoir
```
- ✅ Better memory for huge datasets
- ❌ Loses reason-based diversity

**Head + Tail + Random** (for temporal patterns):
```python
# Keep first N, last N, random middle
head = decisions[:10]
tail = decisions[-10:]
middle = random.sample(decisions[10:-10], 30)
return head + middle + tail
```
- ✅ Preserves "first failure" and "last failure"
- ❌ Loses reason diversity

**Stratified Proportional** (for statistical representativeness):
```python
# Sample proportionally to reason frequency
for reason, items in by_reason.items():
    proportion = len(items) / total
    n = int(proportion * sample_size)
    sampled.extend(random.sample(items, n))
```
- ✅ Statistically representative
- ❌ Rare reasons (potential bugs!) may be undersampled

### Why Our Choice Is Valid for the Assignment

| Concern from Info.md | Our Handling |
|---------------------|--------------|
| "Prohibitively expensive" | Bounded by `threshold` (500) + `per_reason` (50) × reasons |
| "Trade-offs between completeness, performance, storage" | Stats = complete; Decisions = sampled for storage |
| "Who decides?" | Developer configures `threshold` and `per_reason`; system computes stats automatically |

### Scalability Analysis

| Scale | Memory | Time | Verdict |
|-------|--------|------|---------|
| 5,000 decisions | ~500KB | <100ms | ✅ Perfect |
| 50,000 decisions | ~5MB | <1s | ✅ Good |
| 500,000 decisions | ~50MB | ~10s | ⚠️ Borderline |
| 5,000,000 decisions | ~500MB | ~100s | ❌ Need streaming |

### What We'd Add for Production

```python
class ProductionSampler:
    def __init__(self):
        self.max_total = 1000        # Hard cap on output
        self.head_per_reason = 5     # First N (temporal)
        self.tail_per_reason = 5     # Last N (temporal)
        self.streaming_threshold = 100_000  # Switch to reservoir above this
```

### Interview Talking Points

1. **"Why not just keep everything?"** → Storage cost scales linearly. 1M runs × 5000 decisions = 5B rows.

2. **"Why not just keep stats, no decisions?"** → Loses ability to inspect *specific* failures. Can't debug individual cases.

3. **"What if I need the full picture?"** → Configure `threshold` higher. Or store to separate analytics system.

4. **"How do you know N=50 per reason is enough?"** → Configurable. Start with 50, increase if debugging needs more context. Diminishing returns above ~100.

---

## 3. Stats Pre-computation on Step Record

### Decision: Compute and store stats on Step record

We pre-compute statistics (`input_count`, `output_count`, `rejection_rate`, `rejection_reasons_breakdown`) and store them on the Step record.

### Why This Decision?

#### 3.1 Cross-Pipeline Queryability

The assignment asks: *"Show me all runs where the filtering step eliminated more than 90% of candidates—regardless of which pipeline it was."*

Without pre-computed stats, this query would require:
- Scanning all steps
- Unpacking JSONB candidate arrays
- Computing rejection rates on-the-fly

With stats:
```sql
SELECT * FROM steps 
WHERE rejection_rate > 0.9 
AND step_name = 'filtering';
```

**Single index lookup**, no JSONB processing.

#### 3.2 Performance at Scale

As the system grows, we'll have millions of steps. Pre-computed stats enable:
- Fast filtering without full table scans
- Indexes on `rejection_rate`, `input_count`, etc.
- Efficient aggregation queries

#### 3.3 Developer Control

The assignment asks: *"Who decides what gets captured in full vs. summarized—the system or the developer?"*

**Our answer:** The developer controls sampling (via SDK), but the system always computes stats for queryability. This gives:
- Developer control over storage costs (sampling)
- System guarantee of queryability (stats)

### Trade-offs

**What we gave up:**
- **Storage overhead**: Stats take up space on every step record
- **Update complexity**: If we ever need to update candidate data, we'd need to recompute stats

**Mitigation:**
- Stats are small (few integers/floats)
- Candidate data is append-only in practice (debugging system, not transactional)

---

## 4. API Design: Separate Ingest vs Unified Endpoint

### Decision: Separate endpoints for runs and steps

We use:
- `POST /v1/runs` - Create run
- `POST /v1/runs/{run_id}/steps` - Record step
- `PATCH /v1/runs/{run_id}` - Complete run

Rather than a single unified endpoint.

### Why This Decision?

#### 4.1 Clear Separation of Concerns

- **Runs** = Pipeline execution lifecycle
- **Steps** = Decision points within a run
- **Decisions** = Time-ordered events about candidates
- **Evidence** = Additional context for decisions

This matches the mental model developers have and supports our "decisions as events" philosophy.

#### 4.2 Incremental Recording

Pipelines can record steps as they execute, without needing to buffer everything until the end. This enables:
- Real-time debugging (see steps as they happen)
- Memory efficiency (don't hold all data in memory)
- Resilience (if pipeline crashes, completed steps are still recorded)

#### 4.3 Query Flexibility

Separate endpoints make it clear what you're querying:
- `GET /v1/runs` - List runs
- `GET /v1/runs/{run_id}` - Get run with steps
- `POST /v1/query/steps` - Query steps across runs
- `POST /v1/query/decisions` - Query decisions across steps (e.g., "all decisions for candidate X")

### Trade-offs

**What we gave up:**
- **Fewer API calls**: Could batch everything into one call
- **More complex client**: SDK needs to manage run_id across multiple calls

**Mitigation:**
- SDK handles run_id management internally
- Async buffering batches multiple steps into fewer HTTP calls

---

## 5. SDK: Synchronous with Graceful Degradation

### Decision: Synchronous HTTP calls with graceful failure handling

The SDK makes synchronous HTTP calls but handles failures gracefully—the pipeline never breaks due to X-Ray issues.

### Current Implementation

```python
def record_step(self, run_id, step):
    if not self.enabled or run_id is None:
        return None  # Gracefully skip if disabled
    
    try:
        response = self._client.post(f"/v1/runs/{run_id}/steps", json=step_data)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.warning(f"Failed to record X-Ray step: {e}")
        return None  # Pipeline continues even if X-Ray fails
```

### Why This Decision?

#### 5.1 Graceful Degradation

The assignment asks: *"What happens to the pipeline if the X-Ray backend is unavailable?"*

**Our answer:**
- SDK wraps all HTTP calls in try/except
- If API is down, SDK logs warning and returns None
- **Pipeline execution continues uninterrupted**
- Debug data is lost for that run, but pipeline completes

**Pipeline never breaks** due to X-Ray backend issues.

#### 5.2 Simplicity

Synchronous calls are easier to:
- Debug and reason about
- Test and verify
- Implement correctly

For most pipelines, the latency of recording steps is acceptable.

#### 5.3 Immediate Visibility

Steps are visible in the API immediately after recording. No need to wait for buffer flush.

### Trade-offs

**What we gave up:**
- **Performance**: Each `record_step()` blocks on HTTP round-trip
- **Batch efficiency**: Each step is a separate HTTP request

**Mitigation:**
- HTTP client has configurable timeout (default 10s)
- Steps are typically quick (small payloads)
- For high-throughput pipelines, async buffering is a documented future improvement

### Future Improvement: Async Buffering

For production use with high-throughput pipelines, we'd add:
```python
# Future: Non-blocking with background flush
xray.record_step(run_id, step)  # Adds to buffer, returns immediately
# Background thread flushes buffer periodically or on run completion
```

This would provide:
- Non-blocking step recording
- Batch efficiency (multiple steps per HTTP request)
- Memory-bounded buffer with overflow handling

---

## 6. Developer Experience: Explicit vs Implicit

### Decision: Explicit API calls, minimal magic

Developers explicitly call `xray.start_run()`, `xray.record_step()`, `xray.complete_run()`.

### Why This Decision?

#### 6.1 Transparency

Developers can see exactly what's being recorded. No hidden magic, no implicit behavior. This aligns with the debugging nature of the system - you want to know what's happening.

#### 6.2 Control

Developers can:
- Conditionally enable X-Ray: `if xray.enabled: xray.record_step(...)`
- Record partial information: `xray.record_step(run_id, Step(name="...", input={...}))` without output
- Add custom metadata: `xray.start_run(..., metadata={"experiment_id": "abc"})`

#### 6.3 Learning Curve

Explicit API is easier to understand:
- Read the code, see what's recorded
- No need to understand decorator/context manager magic
- Works with any code structure

### Trade-offs

**What we gave up:**
- **Convenience**: More verbose than decorators
- **Auto-capture**: Must manually record inputs/outputs

**Mitigation:**
- SDK provides helper methods: `xray.capture_input()`, `xray.capture_output()`
- Can build wrapper utilities if teams want more automation

---

## 7. Queryability: Cross-Pipeline Queries

### Decision: Convention-based step naming + stats

To enable queries like "all steps where rejection_rate > 0.9", we:
1. Use consistent step naming conventions (documented)
2. Pre-compute stats on every step
3. Store `pipeline_type` on runs for filtering

### Why This Decision?

#### 7.1 The Assignment Requirement

> "A user wants to ask: 'Show me all runs where the filtering step eliminated more than 90% of candidates'—regardless of which pipeline it was."

**Our solution:**
- Developers use consistent step names: `"filtering"`, `"ranking"`, `"keyword_generation"`
- Stats enable efficient queries: `WHERE step_name = 'filtering' AND rejection_rate > 0.9`
- `pipeline_type` allows filtering: `WHERE pipeline_type IN ('competitor_selection', 'listing_optimization')`

#### 7.2 Convention vs Enforcement

We **document conventions** but don't enforce them. This gives:
- Flexibility for custom pipelines
- Guidance for common patterns
- Queryability when conventions are followed

#### 7.3 Future Extensibility

If we need stricter queryability, we can add:
- Step "tags" or "categories" for grouping
- Schema validation for step names
- Query builder that suggests conventions

### Trade-offs

**What we gave up:**
- **Guaranteed queryability**: If developers use inconsistent names, queries won't work
- **Flexibility**: Strict enforcement would limit creative use cases

**Mitigation:**
- Clear documentation with examples
- SDK can validate step names against conventions (optional)
- Query API supports both exact matches and pattern matching

---

## 8. Reasoning Field: First-Class Citizen

### Decision: `reasoning` as a top-level field on Step

We store reasoning as a string field on Step, not buried in metadata.

### Why This Decision?

#### 8.1 The Assignment Emphasis

The assignment repeatedly emphasizes capturing **why** decisions were made:
- "Captures decision context at each step: inputs, candidates, filters applied, outcomes, and **reasoning**"
- "X-Ray answers: *'Why did the system make this decision?'*"

Making `reasoning` a first-class field signals its importance.

#### 8.2 Queryability

Developers can query for steps with reasoning:
```sql
SELECT * FROM steps WHERE reasoning LIKE '%price filter%';
```

If it were in JSONB metadata, queries would be more complex.

#### 8.3 Developer Experience

Explicit field encourages developers to think about and record reasoning:
```python
Step(
    name="filtering",
    reasoning="Applied price cap ($100) and minimum rating (3.5) filters"
)
```

### Trade-offs

**What we gave up:**
- **Structured reasoning**: Can't easily parse structured data from reasoning string
- **Storage**: Takes up space even when empty

**Mitigation:**
- Reasoning can include structured data: `"Applied filters: price<100 (rejected 450), rating>3.5 (rejected 200)"`
- Optional field - can be None
- Can add `reasoning_structured` JSONB field later if needed

---

## 9. Technology Choices

### Decision: Python + FastAPI + PostgreSQL

### Why This Decision?

#### 9.1 Python SDK

- **Widely used** in ML/data pipelines (where non-deterministic systems are common)
- **Rich ecosystem** for async, HTTP clients, data validation (Pydantic)
- **Developer productivity** for rapid prototyping

#### 9.2 FastAPI

- **Async support** for handling concurrent ingest requests
- **Automatic OpenAPI docs** for API exploration
- **Pydantic integration** for request/response validation
- **Performance** comparable to Node.js/Go for this use case

#### 9.3 PostgreSQL

- **JSONB support** for flexible candidate storage
- **Relational queries** for cross-pipeline analysis
- **Mature ecosystem** with excellent Python support (SQLAlchemy, asyncpg)
- **ACID guarantees** for data integrity

### Alternatives Considered

- **MongoDB**: Better for pure document storage, but weaker querying for cross-pipeline analysis
- **ClickHouse**: Better for analytics, but overkill for this use case and adds complexity
- **Redis**: Could work for caching, but not suitable as primary storage

---

## 10. Minimal vs Full Instrumentation

### Decision: Support both minimal and full instrumentation

The SDK supports:
1. **Minimal**: Just record step name and basic I/O
2. **Full**: Record everything - inputs, outputs, config, candidates, reasoning

### Why This Decision?

#### 10.1 The Assignment Question

> "What's the minimal instrumentation to get *something* useful? (b) What does full instrumentation look like?"

**Minimal example:**
```python
xray.record_step(run_id, Step(name="filtering"))
```

**Full example:**
```python
xray.record_step(run_id, Step(
    name="filtering",
    input={"candidate_count": 5000},
    output={"passed_count": 30},
    config={"price_threshold": 100},
    decisions=[...],  # 500 decision events (sampled)
    reasoning="Applied price and rating filters"
))
```

#### 10.2 Progressive Adoption

Teams can start minimal and add detail over time:
- Week 1: Just step names
- Week 2: Add inputs/outputs
- Week 3: Add candidates and reasoning

#### 10.3 Cost Control

Minimal instrumentation = lower storage costs. Teams can choose based on their needs.

---

## 11. Debugging Walkthrough: Phone Case vs Laptop Stand

### The Scenario (from Assignment)

> "A competitor selection run returns a bad match—a **phone case** matched against a **laptop stand**. Using your X-Ray system, how would someone figure out where things went wrong?"

### Step-by-Step Debugging with X-Ray

#### Step 1: Find the Run

```python
# Query: Find runs where the output doesn't match expected category
runs = xray.query_runs(
    pipeline_type="competitor_selection",
    filters={"output.selected_category": "phone_accessories"}
)
# Found: run_id = "abc-123"
```

#### Step 2: Inspect the Run

```python
run = xray.get_run("abc-123")
# Shows:
# - input: {"product_id": "laptop-stand-001", "title": "Adjustable Laptop Stand"}
# - steps: ["keyword_generation", "candidate_search", "filtering", "ranking", "final_selection"]
# - output: {"competitor_id": "phone-case-xyz", "category": "phone_accessories"}
```

**Red flag:** Input is "laptop stand" but output is "phone case". Something went wrong.

#### Step 3: Check Keyword Generation

```python
step = xray.get_step(run_id="abc-123", step_name="keyword_generation")
# Shows:
# - input: {"title": "Adjustable Laptop Stand"}
# - output: {"keywords": ["adjustable", "stand", "holder", "desk accessory"]}
# - reasoning: "Extracted keywords from title, focused on 'adjustable' and 'stand'"
```

**Issue found:** Keywords are too generic. "stand" and "holder" match phone accessories too.

#### Step 4: Check Candidate Search

```python
step = xray.get_step(run_id="abc-123", step_name="candidate_search")
# Shows:
# - input: {"keywords": ["adjustable", "stand", "holder"]}
# - output: {"count": 5000}
# - config: {"limit": 5000}
```

**Issue:** 5000 candidates retrieved—many are phone accessories because "phone stand" and "phone holder" match the keywords.

#### Step 5: Check Filtering Decisions

```python
step = xray.get_step(run_id="abc-123", step_name="filtering")
decisions = xray.get_decisions(step_id=step.id)

# Shows stats:
# - input_count: 5000
# - output_count: 30
# - rejection_rate: 99.4%
# - rejection_reasons_breakdown: {
#     "price_too_high": 2000,
#     "rating_too_low": 1500,
#     "category_mismatch": 1470  # <-- Only 1470 rejected for category!
# }
```

**Issue found:** Category filter was too loose. Many phone accessories passed because:
- "phone stand" has "stand" in title (matched keyword)
- Category was "phone_accessories" but filter only checked top-level category

```python
# Inspect specific decisions
rejected = xray.query_decisions(
    step_id=step.id,
    decision_type="rejected",
    reason="category_mismatch"
)
# Shows examples of what WAS rejected

accepted = xray.query_decisions(
    step_id=step.id,
    decision_type="accepted"
)
# Shows: phone cases, phone stands, AND laptop stands all passed
```

#### Step 6: Check Final Selection

```python
step = xray.get_step(run_id="abc-123", step_name="final_selection")
# Shows:
# - input: {"candidates": 30}
# - output: {"selected_id": "phone-case-xyz", "score": 0.92}
# - reasoning: "Selected based on highest relevance score and keyword match"
# 
# Decisions show:
# - phone-case-xyz: accepted, score=0.92, reason="highest_keyword_overlap"
# - laptop-stand-abc: rejected, score=0.87, reason="lower_score"
```

**Root cause identified:** The phone case scored higher because:
1. Keywords were too generic ("stand", "holder")
2. Category filter was too loose
3. Ranking algorithm weighted keyword overlap too heavily

### Debugging Summary

| Step | What X-Ray Showed | Issue |
|------|-------------------|-------|
| keyword_generation | Generic keywords: "stand", "holder" | Keywords not specific enough |
| candidate_search | 5000 candidates including phone accessories | Broad keywords = broad results |
| filtering | Only 1470 rejected for category mismatch | Category filter too loose |
| final_selection | Phone case scored higher on keyword overlap | Ranking over-weighted keywords |

### What to Fix

1. **Keyword generation**: Add category-aware keyword extraction
2. **Filtering**: Stricter category matching (subcategory level)
3. **Ranking**: Weight category match higher than keyword overlap

---

## 12. SOLID, KISS, and DRY Principles

### SOLID Principles

| Principle | How We Apply It |
|-----------|-----------------|
| **S**ingle Responsibility | Each component has one job: `XRay` (client), `Sampler` (sampling), `Buffer` (async), `StatsComputer` (stats) |
| **O**pen/Closed | Models are extensible via `metadata` JSONB fields without modifying core schema |
| **L**iskov Substitution | Decision types (accepted/rejected/pending) are interchangeable in processing logic |
| **I**nterface Segregation | Separate API routes for ingest vs query; SDK has focused methods |
| **D**ependency Inversion | SDK depends on HTTP client abstraction, not concrete implementation; allows mocking in tests |

#### Single Responsibility in Practice

```python
# Each class does ONE thing
class XRay:
    """Client for recording runs and steps"""
    
class Sampler:
    """Samples decisions to reduce data volume"""
    
class Buffer:
    """Buffers and batches HTTP requests"""
    
class StatsComputer:
    """Computes statistics from decisions"""
```

#### Open/Closed in Practice

```python
# Core model is closed for modification
class Decision(BaseModel):
    candidate_id: str
    decision_type: Literal["accepted", "rejected", "pending"]
    reason: str | None
    score: float | None
    metadata: dict[str, Any] | None  # <-- Open for extension
```

New fields can be added via `metadata` without changing the schema:
```python
Decision(
    candidate_id="abc",
    decision_type="rejected",
    reason="price_too_high",
    metadata={"custom_field": "value", "experiment_id": "exp-001"}
)
```

### KISS (Keep It Simple, Stupid)

| Aspect | How We Keep It Simple |
|--------|----------------------|
| Data Model | 4 tables: Run → Step → Decision → Evidence |
| SDK API | 3 core methods: `start_run()`, `record_step()`, `complete_run()` |
| Decision Types | Simple enum: accepted / rejected / pending |
| Sampling | Clear rule: all accepted + N per rejection reason |

#### Simplicity in API Design

```python
# Minimal API surface - 3 methods cover all use cases
run_id = xray.start_run(pipeline_type="...", input={...})
xray.record_step(run_id, Step(...))
xray.complete_run(run_id, result={...})
```

No decorators, no magic, no hidden state. Read the code, understand what's recorded.

### DRY (Don't Repeat Yourself)

| Aspect | How We Avoid Repetition |
|--------|------------------------|
| Models | Pydantic models shared between SDK and API (single source of truth) |
| Stats Computation | Single `StatsComputer` class, reused everywhere |
| Sampling Logic | Single `Sampler` class with configurable strategy |
| Validation | Pydantic validates once at model creation |

#### DRY in Practice

```python
# Same models used in SDK and API
# xray/models.py - shared
from pydantic import BaseModel

class Decision(BaseModel):
    candidate_id: str
    decision_type: Literal["accepted", "rejected", "pending"]
    # ...

# SDK uses it
from xray.models import Decision
decisions.append(Decision(candidate_id="abc", ...))

# API uses the same model
from xray.models import Decision
@app.post("/v1/runs/{run_id}/steps")
def create_step(step: StepCreate):
    # step.decisions uses the same Decision model
```

---

## 13. What Next: Future Improvements

### If Shipping for Real-World Use

The assignment asks: *"If you were to ship this SDK for real world use cases, what are other technical aspects you would want to work on?"*

#### 13.1 Observability & Monitoring

| Feature | Why |
|---------|-----|
| **SDK metrics** | Track recording latency, buffer size, API errors |
| **API metrics** | Request rates, P99 latency, error rates |
| **Alerting** | Alert when rejection rates spike unexpectedly |
| **Dashboards** | Visualize pipeline health across runs |

#### 13.2 Storage & Retention

| Feature | Why |
|---------|-----|
| **TTL policies** | Auto-delete runs older than N days |
| **Tiered storage** | Move old data to cold storage (S3) |
| **Compression** | Compress JSONB fields for older records |
| **Partitioning** | Partition tables by date for query performance |

#### 13.3 Security & Multi-tenancy

| Feature | Why |
|---------|-----|
| **API authentication** | API keys or OAuth for SDK clients |
| **Tenant isolation** | Separate data per team/organization |
| **PII handling** | Redact sensitive data before storage |
| **Audit logging** | Track who accessed what data |

#### 13.4 Developer Experience

| Feature | Why |
|---------|-----|
| **CLI tool** | `xray query --pipeline=competitor_selection --rejection-rate=">0.9"` |
| **Web UI** | Visual explorer for runs, steps, decisions |
| **VS Code extension** | View X-Ray data alongside code |
| **Notebooks integration** | Jupyter widgets for exploration |

#### 13.5 Advanced Querying

| Feature | Why |
|---------|-----|
| **Full-text search** | Search reasoning fields |
| **Anomaly detection** | Flag runs that deviate from normal patterns |
| **Comparison queries** | "Compare run A to run B" |
| **Aggregation queries** | "Average rejection rate by step type" |

#### 13.6 SDK Improvements

| Feature | Why |
|---------|-----|
| **Multi-language SDKs** | JavaScript, Go, Java versions |
| **Automatic instrumentation** | OpenTelemetry integration |
| **Offline mode** | Buffer to disk when API unavailable |
| **Sampling policies** | Configurable per-pipeline sampling rules |

---

## 13.5. Current Limitations & Scalability Acknowledgment

### Honest Assessment: What Doesn't Scale

The current implementation is optimized for the **assignment's use case** (5000→30 candidates), not production scale (millions of decisions). Here's what would break and why we didn't fix it yet:

#### Limitation 1: O(n) Memory in Sampling

**Current Behavior:**
```python
def sample(self, decisions: list[Decision]):
    # Must load ALL decisions into memory first
    for d in decisions:  # O(n) memory
        if d.decision_type == "rejected":
            rejected_by_reason[d.reason].append(d)
```

**Why It Breaks at Scale:**
| Decisions | Memory Required | Time |
|-----------|-----------------|------|
| 5,000 | ~500 KB | <100ms |
| 500,000 | ~50 MB | ~10s |
| 5,000,000 | ~500 MB | ~100s |
| 50,000,000 | ~5 GB | OOM crash |

**Why We Didn't Implement Streaming:**
- **Complexity vs. benefit**: Reservoir sampling per reason requires tracking multiple reservoirs, handling reason discovery mid-stream, and careful memory management.
- **Assignment scope**: The assignment explicitly mentions "5000 candidates" — our solution handles 10-100x that comfortably.
- **YAGNI**: Building for 50M when you need 5K is over-engineering.

**Production Fix:**
```python
class StreamingSampler:
    """O(k) memory where k = sample size, not n = total decisions"""
    def __init__(self):
        self.reservoirs: dict[str, list] = defaultdict(list)  # One per reason
        self.counts: dict[str, int] = defaultdict(int)
    
    def add(self, decision: Decision):
        """Process one decision at a time"""
        reason = decision.reason or "unknown"
        self.counts[reason] += 1
        
        # Reservoir sampling
        if len(self.reservoirs[reason]) < self.per_reason:
            self.reservoirs[reason].append(decision)
        else:
            j = random.randint(0, self.counts[reason] - 1)
            if j < self.per_reason:
                self.reservoirs[reason][j] = decision
```

#### Limitation 2: Synchronous HTTP Calls

**Current Behavior:**
```python
def record_step(self, run_id, step):
    response = self._client.post(...)  # Blocks until complete
    return response.json()
```

**Why It Breaks at Scale:**
- Each step recording adds ~50-200ms latency to the pipeline
- 10 steps = 0.5-2 seconds added to every run
- High-throughput pipelines (1000 runs/sec) would bottleneck on X-Ray

**Why We Didn't Implement Async Buffering:**
- **Complexity**: Requires background threads, queue management, flush-on-exit handling, error recovery
- **Debugging clarity**: Synchronous is easier to debug — you know exactly when data is recorded
- **Assignment fit**: Demo pipelines run once, not 1000/sec

**Production Fix:**
```python
class AsyncXRay:
    def __init__(self):
        self.buffer = queue.Queue(maxsize=1000)
        self.flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self.flush_thread.start()
    
    def record_step(self, run_id, step):
        self.buffer.put((run_id, step))  # Non-blocking
        return None  # No immediate response
    
    def _flush_loop(self):
        while True:
            batch = []
            while len(batch) < 100 and not self.buffer.empty():
                batch.append(self.buffer.get())
            if batch:
                self._send_batch(batch)
            time.sleep(0.1)
```

#### Limitation 3: Unbounded Sampling Output

**Current Behavior:**
```python
# 20 reasons × 50 per reason = 1000 decisions (exceeds threshold!)
for reason, items in rejected_by_reason.items():
    sampled.extend(random.sample(items, min(50, len(items))))
```

**Why It's a Problem:**
- More diverse rejection reasons = larger output
- No hard cap on total sampled decisions
- Pathological case: 100 unique reasons × 50 = 5000 decisions (no reduction!)

**Why We Didn't Implement Hard Cap:**
- **Reasoning preservation**: Hard cap forces dropping entire reasons
- **Assignment clarity**: Simple algorithm is easier to explain and verify
- **Real-world patterns**: Most filtering steps have 3-10 reasons, not 100

**Production Fix:**
```python
def sample_with_cap(self, decisions, max_total=1000):
    # ... existing logic ...
    
    if len(sampled) > max_total:
        # Prioritize: all accepted, then proportional rejected per reason
        accepted = [d for d in sampled if d.decision_type == "accepted"]
        if len(accepted) >= max_total:
            return random.sample(accepted, max_total)
        
        remaining = max_total - len(accepted)
        rejected = [d for d in sampled if d.decision_type == "rejected"]
        return accepted + random.sample(rejected, remaining)
```

#### Limitation 4: No Database Partitioning

**Current Behavior:**
- Single `decisions` table with all decisions
- Indexes on `candidate_id`, `step_id`, `reason`

**Why It Breaks at Scale:**
| Runs/Day | Decisions/Day | After 1 Year |
|----------|---------------|--------------|
| 100 | 50K | 18M rows |
| 10,000 | 5M | 1.8B rows |

- Query performance degrades as table grows
- Index maintenance becomes expensive
- Backups take hours

**Why We Didn't Implement Partitioning:**
- **SQLite**: Default database doesn't support partitioning
- **Scope**: Assignment runs once, no retention concerns
- **Complexity**: Partitioning requires PostgreSQL + migration strategy

**Production Fix:**
```sql
-- PostgreSQL partition by date
CREATE TABLE decisions (
    id UUID,
    step_id UUID,
    created_at TIMESTAMP,
    ...
) PARTITION BY RANGE (created_at);

CREATE TABLE decisions_2024_01 PARTITION OF decisions
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

### Why Current Implementation Is Valid

| Assignment Requirement | Our Implementation | Sufficient? |
|------------------------|-------------------|-------------|
| "5000 candidates filtered to 30" | O(n) sampling with n=5000 | ✅ Yes |
| "Trade-offs between completeness and storage" | Stats complete, decisions sampled | ✅ Yes |
| "What happens if backend unavailable?" | Graceful degradation, pipeline continues | ✅ Yes |
| "General-purpose SDK" | Domain-agnostic, functional API | ✅ Yes |

**Key Point for Interview:**
> "I built for the problem at hand, not hypothetical scale. The architecture supports future scaling — streaming sampling, async buffering, partitioning — but implementing them now would be premature optimization. The current solution handles 10-100x the assignment's stated scale."

---

## 14. Requirements Mapping: Plan vs Assignment

### Coverage Checklist

| Requirement (from Info.md) | How We Address It | Section |
|---------------------------|-------------------|---------|
| Lightweight wrapper | Functional SDK with 3 core methods | Section 1, 6 |
| Captures inputs | `Step.input` field | Section 10 |
| Captures candidates | `Decision` model with `candidate_id` | Section 2 |
| Captures filters applied | `Step.config` field | Section 10 |
| Captures outcomes | `Decision.decision_type` + `Step.output` | Section 2 |
| Captures reasoning | `Step.reasoning` + `Decision.reason` | Section 8 |
| General-purpose | Functional API, domain-agnostic models | Section 1 |
| Ingest API | POST /v1/runs, /steps, PATCH /runs | Section 4 |
| Query API | GET /runs, POST /query/steps, /query/decisions | Section 4, 7 |
| Data model rationale | Decisions as events, not entities | Section 2 |
| Debugging walkthrough | Phone case vs laptop stand example | Section 11 |
| Cross-pipeline queries | Stats + conventions + step_name filters | Section 3, 7 |
| Scale (5000 → 30) | Decision sampling, stats pre-computation | Section 2, 3 |
| Developer experience | Minimal vs full instrumentation | Section 10 |
| Backend unavailability | Async buffering, graceful degradation | Section 5 |

### What's Different from Traditional Tracing

The assignment emphasizes this distinction:

| Aspect | Traditional Tracing | Our X-Ray System |
|--------|---------------------|------------------|
| Focus | Performance & flow | Decision reasoning |
| Data | Spans, timing, service calls | Decisions, candidates, filters |
| Question answered | "What happened?" | "Why this output?" |
| Granularity | Function/service level | Business logic level |

**Our system answers:** *"Why did the system select a phone case when the input was a laptop stand?"*

---

## Summary: Key Design Principles

1. **General-Purpose First**: Decouple from code structure, work with any architecture
2. **Decisions Over Entities**: Model decisions as time-ordered events, not candidate states
3. **Explicit Over Implicit**: Developers see what's recorded, no hidden magic
4. **Queryability**: Pre-compute stats, use conventions, enable cross-pipeline analysis
5. **Scale Awareness**: Decision-level sampling, async buffering, efficient storage
6. **Developer Control**: Minimal to full instrumentation, graceful degradation
7. **Reasoning as First-Class**: Capture *why* decisions were made, not just *what*
8. **SOLID/KISS/DRY**: Follow engineering principles for maintainable, extensible code

---

## Questions to Prepare For

Based on the assignment, be ready to explain:

### Core Design Questions

1. **"Why functional over context manager?"** 
   → General-purpose, decision context capture, scale handling (Section 1)

2. **"Why Decisions as events instead of Candidates as entities?"** 
   → One candidate can have multiple decisions, preserves timeline, sampling preserves reasoning (Section 2)

3. **"How do you handle 5000 → 30 candidates?"** 
   → Sample decisions (all accepted + N per reason), pre-compute stats (Section 2, 3)

4. **"How do you query across pipelines?"** 
   → Conventions + stats, step_name + rejection_rate queries (Section 3, 7)

### Operational Questions

5. **"What if backend is unavailable?"** 
   → Async buffering, graceful degradation, pipeline continues (Section 5)

6. **"Minimal vs full instrumentation?"** 
   → Progressive adoption, cost control, examples of both (Section 10)

7. **"How would you retrofit this into an existing system?"** 
   → Minimal wrapping, explicit API, no code restructuring needed (Section 1, 6)

### Architecture Questions

8. **"Why PostgreSQL over MongoDB?"** 
   → Relational queries for cross-pipeline analysis, JSONB for flexibility (Section 9)

9. **"How do you follow SOLID principles?"** 
   → Single responsibility components, open for extension via metadata, interface segregation (Section 12)

10. **"Walk me through debugging a bad match"** 
    → Phone case vs laptop stand example (Section 11)

### Future-Looking Questions

11. **"What would you add for production use?"** 
    → Observability, retention policies, multi-tenancy, security (Section 13)

12. **"How does this differ from traditional tracing?"** 
    → Decision reasoning vs performance tracking, business logic vs function calls (Section 14)