# Data Structure Decisions & Justifications

This document explains **why** each data structure was chosen in the X-Ray implementation, alternatives considered, and trade-offs made.

---

## 1. Decisions: List (Array) vs Dictionary vs Set

### Current: `list[Decision]`

```python
class Step(BaseModel):
    decisions: list[Decision] | None = Field(None)
```

### Why List/Array?

| Property | Why It Matters |
|----------|----------------|
| **Order Preserved** | Decisions happen in sequence. "Candidate A was rejected before B" is meaningful for debugging. |
| **Duplicates Allowed** | Same candidate can appear multiple times (evaluated → rejected → reconsidered → accepted). |
| **Iteration** | We iterate through all decisions for stats computation and sampling. |
| **JSON Serializable** | Arrays map directly to JSON arrays for API transport. |

### Alternatives Considered

| Alternative | Why Not |
|-------------|---------|
| **`dict[candidate_id, Decision]`** | ❌ Loses order. One candidate can have only one decision. We need to track multiple decisions per candidate. |
| **`set[Decision]`** | ❌ Sets require hashable items, lose order, and don't allow duplicates. Decisions aren't naturally hashable. |
| **`dict[candidate_id, list[Decision]]`** | ⚠️ Possible but adds complexity. We'd need to flatten for iteration and sampling. Order across candidates is lost. |

### The Key Insight

**Decisions are time-ordered events, not a lookup table.**

```python
# What we need to express:
decisions = [
    Decision(candidate_id="A", decision_type="rejected", reason="price_too_high"),   # t=1
    Decision(candidate_id="B", decision_type="accepted", score=0.9),                  # t=2
    Decision(candidate_id="A", decision_type="accepted", reason="price_dropped"),    # t=3 (reconsidered!)
]

# A dictionary would lose this timeline:
# decisions_dict = {"A": ???, "B": ???}  # Which decision for A?
```

---

## 2. Sampling Algorithm: Critical Analysis

### Current Implementation

```python
# Strategy: Keep ALL accepted + N rejected PER REASON
def sample(self, decisions: list[Decision]) -> tuple[list[Decision], bool]:
    if len(decisions) <= self.threshold:  # Default: 500
        return decisions, False
    
    accepted = []
    rejected_by_reason: dict[str, list[Decision]] = defaultdict(list)
    pending = []
    
    for d in decisions:
        if d.decision_type == "accepted":
            accepted.append(d)
        elif d.decision_type == "rejected":
            rejected_by_reason[d.reason or "unknown"].append(d)
        else:
            pending.append(d)
    
    # Sample N (default: 50) per reason
    sampled_rejected = []
    for reason, reason_decisions in rejected_by_reason.items():
        if len(reason_decisions) <= self.per_reason:
            sampled_rejected.extend(reason_decisions)
        else:
            sampled_rejected.extend(random.sample(reason_decisions, self.per_reason))
    
    return accepted + sampled_rejected + pending, True
```

### Why This Algorithm?

| Design Choice | Rationale |
|---------------|-----------|
| **Keep ALL accepted** | Winners matter most for debugging. "Why did this pass?" is a common question. |
| **N per rejection reason** | Preserves *reasoning diversity*. If 2000 rejected for "price_too_high" and 500 for "rating_too_low", both reasons are represented. |
| **Random within reason** | Simple, unbiased selection within each category. |
| **Stats computed BEFORE sampling** | Accurate counts for queries (rejection_rate reflects true rate, not sampled rate). |

### Strengths ✅

| Strength | Why It Matters |
|----------|----------------|
| **Reasoning diversity preserved** | You never lose *why* things were rejected, just volume. |
| **Simple to understand** | Developers can predict what gets kept. |
| **Configurable** | `threshold` and `per_reason` are adjustable per use case. |
| **Stats remain accurate** | Pre-computed before sampling, so queries work correctly. |

### Weaknesses & Limitations ⚠️

| Weakness | Impact | Severity |
|----------|--------|----------|
| **O(n) memory** | Must load ALL decisions into memory before sampling. 5000 decisions = fine. 5 million = OOM. | 🔴 High for extreme scale |
| **Random loses time distribution** | `random.sample()` within each reason loses temporal patterns. Early vs late rejections might differ. | 🟡 Medium |
| **Unbounded output** | With k reasons × N per_reason, output can exceed threshold. 20 reasons × 50 = 1000 decisions. | 🟡 Medium |
| **No importance weighting** | Borderline decisions (score=0.49 vs threshold=0.5) treated same as clear rejections (score=0.1). | 🟡 Medium |
| **Equal weight per reason** | Rare reasons (5 occurrences) get same N as common reasons (2000 occurrences). | 🟢 Low (often desirable) |

### Alternative Algorithms Considered

#### 1. **Reservoir Sampling** (Better for Streaming)

```python
# O(k) memory, O(n) time, processes items one-by-one
import random

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

| Property | Our Approach | Reservoir Sampling |
|----------|--------------|-------------------|
| Memory | O(n) | O(k) where k = sample size |
| Streaming support | ❌ No | ✅ Yes |
| Reason preservation | ✅ Yes | ❌ No (pure random) |
| Implementation | Simple | Simple |

**Verdict**: Reservoir is better for memory, but loses reason-based diversity. For 5000→30 problem, our approach is fine. For 5M→500 problem, consider reservoir per reason.

#### 2. **Head + Tail + Random** (Better for Time Distribution)

```python
def head_tail_random_sample(decisions, head=10, tail=10, random_count=30):
    if len(decisions) <= head + tail + random_count:
        return decisions
    
    head_items = decisions[:head]           # First N (early decisions)
    tail_items = decisions[-tail:]          # Last N (late decisions)
    middle = decisions[head:-tail]
    random_items = random.sample(middle, random_count)
    
    return head_items + random_items + tail_items
```

| Property | Our Approach | Head+Tail+Random |
|----------|--------------|------------------|
| Preserves temporal boundaries | ❌ No | ✅ Yes |
| Preserves reason diversity | ✅ Yes | ❌ No |
| Identifies "first failure" | ❌ No | ✅ Yes |

**Verdict**: Useful for debugging "when did failures start?" but loses reason diversity.

#### 3. **Stratified Proportional Sampling** (Statistically Representative)

```python
def stratified_sample(decisions, total_sample_size=500):
    # Sample proportionally to reason frequency
    by_reason = defaultdict(list)
    for d in decisions:
        by_reason[d.reason].append(d)
    
    total = len(decisions)
    sampled = []
    for reason, items in by_reason.items():
        proportion = len(items) / total
        n = max(1, int(proportion * total_sample_size))
        sampled.extend(random.sample(items, min(n, len(items))))
    
    return sampled
```

| Property | Our Approach | Stratified |
|----------|--------------|------------|
| Rare reasons preserved | ✅ Equal weight | ⚠️ May be undersampled |
| Statistical representation | ⚠️ Not proportional | ✅ Proportional |
| Total output bounded | ❌ No | ✅ Yes |

**Verdict**: Better for statistical analysis, but rare reasons (which might be bugs!) could be lost.

#### 4. **Importance Sampling** (Better for Edge Cases)

```python
def importance_sample(decisions, threshold_score=0.5, per_reason=50):
    by_reason = defaultdict(list)
    for d in decisions:
        by_reason[d.reason].append(d)
    
    sampled = []
    for reason, items in by_reason.items():
        # Sort by distance to threshold (closer = more interesting)
        items.sort(key=lambda d: abs((d.score or 0) - threshold_score))
        sampled.extend(items[:per_reason])  # Keep closest to threshold
    
    return sampled
```

| Property | Our Approach | Importance |
|----------|--------------|------------|
| Edge cases preserved | ❌ Random | ✅ Prioritized |
| Clear rejections | ✅ Included | ⚠️ May be dropped |
| Requires score field | ❌ No | ✅ Yes |

**Verdict**: Excellent for debugging "why did borderline cases fail?" but requires score metadata.

### What Would Be Ideal for Production?

```python
class ProductionSampler:
    """
    Hybrid sampling strategy for production use.
    
    Combines:
    1. All accepted (preserve winners)
    2. Head + Tail per reason (preserve temporal boundaries)
    3. Importance-weighted middle (preserve edge cases)
    4. Hard cap on total output (bound storage)
    """
    
    def __init__(
        self,
        threshold: int = 500,
        per_reason: int = 50,
        head_per_reason: int = 5,
        tail_per_reason: int = 5,
        max_total: int = 1000,  # Hard cap
        importance_field: str = "score"
    ):
        self.threshold = threshold
        self.per_reason = per_reason
        self.head_per_reason = head_per_reason
        self.tail_per_reason = tail_per_reason
        self.max_total = max_total
        self.importance_field = importance_field
    
    def sample(self, decisions: list[Decision]) -> list[Decision]:
        if len(decisions) <= self.threshold:
            return decisions
        
        # 1. Keep all accepted
        accepted = [d for d in decisions if d.decision_type == "accepted"]
        
        # 2. Group rejected by reason, preserving order
        rejected_by_reason = defaultdict(list)
        for i, d in enumerate(decisions):
            if d.decision_type == "rejected":
                d._original_index = i  # Track position
                rejected_by_reason[d.reason or "unknown"].append(d)
        
        # 3. For each reason: head + tail + importance-weighted middle
        sampled_rejected = []
        for reason, items in rejected_by_reason.items():
            items.sort(key=lambda d: d._original_index)  # Restore order
            
            if len(items) <= self.per_reason:
                sampled_rejected.extend(items)
            else:
                head = items[:self.head_per_reason]
                tail = items[-self.tail_per_reason:]
                middle = items[self.head_per_reason:-self.tail_per_reason]
                
                # Importance-weighted sampling of middle
                remaining = self.per_reason - len(head) - len(tail)
                if middle and remaining > 0:
                    # Sort by score proximity to threshold (if available)
                    middle.sort(key=lambda d: -abs(d.score or 0.5))
                    middle_sample = middle[:remaining]
                else:
                    middle_sample = []
                
                sampled_rejected.extend(head + middle_sample + tail)
        
        # 4. Apply hard cap
        result = accepted + sampled_rejected
        if len(result) > self.max_total:
            # Prioritize: all accepted, then sample rejected
            if len(accepted) >= self.max_total:
                result = random.sample(accepted, self.max_total)
            else:
                remaining = self.max_total - len(accepted)
                result = accepted + random.sample(sampled_rejected, remaining)
        
        # 5. Sort by original sequence
        result.sort(key=lambda d: getattr(d, '_original_index', 0))
        
        return result
```

### Why Our Current Implementation Is Valid

For the assignment's **5000→30 problem**:

| Concern | Our Handling | Sufficient? |
|---------|--------------|-------------|
| **Memory** | O(5000) = ~500KB | ✅ Yes |
| **Reasoning preserved** | N per reason | ✅ Yes |
| **Stats accurate** | Computed before sampling | ✅ Yes |
| **Configurable** | threshold, per_reason | ✅ Yes |

**For production scale (millions of decisions)**:

| Concern | Current | Needed |
|---------|---------|--------|
| Memory | O(n) | O(k) streaming |
| Hard cap | ❌ No | ✅ Yes |
| Importance weighting | ❌ No | ⚠️ Nice to have |

### Scalability Verdict

| Scale | Current Implementation | Verdict |
|-------|------------------------|---------|
| **5,000 decisions** | Works perfectly | ✅ |
| **50,000 decisions** | Works, ~5MB memory | ✅ |
| **500,000 decisions** | Works, ~50MB memory | ⚠️ Borderline |
| **5,000,000 decisions** | OOM risk, ~500MB | ❌ Needs streaming |

**Recommendation for production**: Add streaming mode with reservoir sampling per reason for datasets > 100K decisions.

### Complexity Analysis (Grouping Structure)

```
Current approach:
- Build grouping: O(n) - single pass through decisions
- Sample per reason: O(k * m) where k=reasons, m=decisions_per_reason
- Total: O(n)

Alternative (filter each time):
- For each reason, filter: O(n)
- Total: O(k * n) where k=reasons
```

With 5000 decisions and 10 reasons: **5,000 vs 50,000 operations**.

---

## 3. Stats: Dictionary vs Dedicated Class

### Current: `dict[str, Any]`

```python
stats: dict[str, Any] | None = mapped_column(JSON, nullable=True)

# Example:
{
    "input_count": 5000,
    "output_count": 30,
    "rejection_rate": 0.994,
    "rejection_reasons": {"price_too_high": 2000, "rating_too_low": 1500}
}
```

### Why Dictionary/JSON?

| Property | Why It Matters |
|----------|----------------|
| **Flexible schema** | Different steps may have different stats. Filtering step has rejection_reasons; ranking step might have score_distribution. |
| **Direct DB storage** | JSON columns in PostgreSQL/SQLite store this natively. No ORM complexity. |
| **API transport** | Serializes directly to JSON response. |
| **Queryable** | PostgreSQL JSONB supports indexing and querying: `stats->>'rejection_rate'` |

### Alternatives Considered

| Alternative | Trade-off |
|-------------|-----------|
| **`StepStats` class with fixed fields** | ✅ Type safety, IDE autocomplete. ❌ Can't add new stats without schema change. |
| **Separate `stats` table with key-value pairs** | ❌ Overly normalized. Simple stats become multiple rows. Query complexity increases. |
| **Flatten into Step columns** | ❌ Fixed schema. Every step would have all columns even if unused. |

### We Also Have a Pydantic Model for Validation

```python
class StepStats(BaseModel):
    """Pre-computed statistics for a step."""
    input_count: int = Field(0)
    output_count: int = Field(0)
    rejection_rate: float = Field(0.0)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
```

**Best of both worlds**: Validate at SDK level, store flexibly in DB.

---

## 4. Input/Output/Config: `dict[str, Any]`

### Current:

```python
input: dict[str, Any] | None   # {"candidate_count": 5000, "keywords": ["laptop", "stand"]}
output: dict[str, Any] | None  # {"passed_count": 30, "winner_id": "prod-123"}
config: dict[str, Any] | None  # {"price_threshold": 100, "min_rating": 3.5}
```

### Why Dictionary?

| Property | Why It Matters |
|----------|----------------|
| **Domain-agnostic** | X-Ray doesn't know your domain. Competitor selection has different inputs than content generation. |
| **Extensible** | Add new fields without schema migration. |
| **Self-documenting** | Keys describe what the data means: `{"price_threshold": 100}` vs `[100]`. |

### Why Not Typed Models?

```python
# This would NOT be general-purpose:
class FilteringStepInput(BaseModel):
    candidate_count: int
    keywords: list[str]
    price_threshold: float
```

**Problem**: Every new pipeline type would need new models. X-Ray would become domain-specific.

### Trade-off

| Approach | Type Safety | Flexibility | Domain Agnostic |
|----------|-------------|-------------|-----------------|
| `dict[str, Any]` | ❌ None | ✅ High | ✅ Yes |
| Typed Pydantic models | ✅ Full | ❌ Low | ❌ No |
| Union of known types | ⚠️ Partial | ⚠️ Medium | ⚠️ Somewhat |

**Our choice**: Flexibility and domain-agnosticism over type safety. This matches the assignment requirement for a "general-purpose" SDK.

---

## 5. Database: Relational Tables with JSON Columns

### Current Schema:

```
runs (relational)
├── id (PK)
├── pipeline_type (indexed)
├── status (indexed)
├── input_context (JSON)      ← Flexible
├── output_result (JSON)      ← Flexible
└── metadata (JSON)           ← Flexible

steps (relational)
├── id (PK)
├── run_id (FK, indexed)
├── step_name (indexed)
├── sequence_order (int)
├── input_data (JSON)         ← Flexible
├── output_data (JSON)        ← Flexible
├── config (JSON)             ← Flexible
├── stats (JSON)              ← Flexible
└── reasoning (text)

decisions (relational)
├── id (PK)
├── step_id (FK, indexed)
├── candidate_id (indexed)    ← Queryable!
├── decision_type (indexed)   ← Queryable!
├── reason (indexed)          ← Queryable!
├── score (float)
├── sequence_order (int)
└── metadata (JSON)           ← Flexible
```

### Why Hybrid (Relational + JSON)?

| Field Type | Storage | Why |
|------------|---------|-----|
| **IDs, FKs** | Relational columns | Joins, referential integrity |
| **Indexed fields** (candidate_id, reason, status) | Relational columns with index | Fast WHERE queries |
| **Flexible/variable data** (input, output, config, metadata) | JSON columns | Schema flexibility |

### Alternatives Considered

| Alternative | Trade-off |
|-------------|-----------|
| **Pure relational** | ❌ Need schema migration for every new field. Inflexible. |
| **Pure document DB (MongoDB)** | ❌ Loses relational queries. Can't efficiently do "all decisions for candidate X across all runs". |
| **Key-value store** | ❌ No querying capability. Can only fetch by ID. |
| **Pure JSON in one table** | ❌ Loses relationships. Can't query decisions separately from steps. |

### Why Decisions Are a Separate Table (Not Embedded JSON)

**Option A: Embedded JSON** (Not chosen)
```python
step.decisions = JSON([{...}, {...}, ...])  # All decisions in one JSON blob
```

**Option B: Separate Table** (Chosen)
```sql
SELECT * FROM decisions WHERE candidate_id = 'prod-123';
-- Works across ALL steps and runs!
```

**Reason**: We need to query decisions across runs. "Show all decisions for candidate X" is impossible with embedded JSON without full table scans.

---

## 6. Evidence: Separate Table vs Embedded

### Current:

```sql
evidence (separate table)
├── id
├── decision_id (FK)
├── evidence_type
├── data (JSON)
```

### Why Separate Table?

| Reason | Explanation |
|--------|-------------|
| **Heavy payloads** | LLM outputs can be large (tokens, full response text). Separating keeps Decision records light. |
| **Optional** | Not every decision has evidence. Embedding would waste space. |
| **Queryable by type** | "Show all LLM evidence" is a valid query. |
| **Lazy loading** | Fetch decisions without evidence, then load evidence on demand. |

### Alternative: Embed in Decision

```python
class Decision(BaseModel):
    evidence: list[Evidence] | None  # Embedded
```

**Problem**: Every decision query loads all evidence. 5000 decisions with LLM outputs = huge payload.

---

## 7. Sequence Order: Integer vs Timestamp

### Current: `sequence_order: int`

```python
for idx, decision in enumerate(decisions):
    Decision(..., sequence_order=idx)
```

### Why Integer Over Timestamp?

| Property | Integer | Timestamp |
|----------|---------|-----------|
| **Deterministic ordering** | ✅ Always unique | ❌ Can have ties (same millisecond) |
| **Meaningful** | ✅ "3rd decision" | ⚠️ "2024-01-01T12:00:00.123Z" |
| **Space efficient** | ✅ 4 bytes | ❌ 8 bytes |
| **No clock issues** | ✅ Doesn't depend on system time | ❌ Clock skew can cause issues |

### We Also Store Timestamp

`created_at: datetime` captures when, but `sequence_order` captures order within the step.

---

## 8. String IDs vs UUIDs vs Auto-increment

### Current: UUID strings

```python
id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
```

### Why UUID Strings?

| Property | Why It Matters |
|----------|----------------|
| **Globally unique** | No collisions across distributed systems. |
| **Client-generated** | SDK can generate ID before API call (useful for async buffering future). |
| **No coordination** | No need for central ID generator. |
| **URL-safe** | UUIDs work in URLs without encoding. |

### Alternatives

| Alternative | Trade-off |
|-------------|-----------|
| **Auto-increment integer** | ❌ Sequential, predictable. Requires DB coordination. Can't generate client-side. |
| **UUID binary** | ✅ Smaller (16 bytes vs 36). ❌ Less readable in logs/debugging. |
| **ULID** | ✅ Sortable by time. ⚠️ Less common, needs library. |

---

## Summary: Data Structure Choices

| Component | Structure | Justification |
|-----------|-----------|---------------|
| **Decisions in Step** | `list[Decision]` | Order matters, duplicates possible, time-series of events |
| **Grouping by reason** | `dict[str, list[Decision]]` | O(1) lookup for sampling "N per reason" |
| **Stats** | `dict[str, Any]` (JSON) | Flexible schema, step-type agnostic |
| **Input/Output/Config** | `dict[str, Any]` (JSON) | Domain agnostic, extensible |
| **Decisions storage** | Separate relational table | Cross-run queries, indexing, relationships |
| **Evidence storage** | Separate table with JSON data | Heavy payloads, optional, lazy loading |
| **Sequence** | Integer | Deterministic, no ties, meaningful |
| **IDs** | UUID strings | Global uniqueness, client-generatable |

---

## Key Principle: Optimize for Debugging Queries

Every data structure choice optimizes for these queries:

1. **"Why was this candidate rejected?"** → Decisions as events with reasons
2. **"Show all decisions for candidate X across all runs"** → Separate decisions table with index
3. **"Find all filtering steps with >90% rejection rate"** → Pre-computed stats in JSON
4. **"What was the sequence of decisions?"** → Integer sequence_order + list ordering
5. **"Show LLM evidence for this decision"** → Separate evidence table, lazy loadable

---

## When Would We Change These?

| Scenario | Change |
|----------|--------|
| **Need type safety on inputs** | Add optional Pydantic validation schemas per pipeline_type |
| **Need time-series queries** | Add timestamp-based indexing, consider time-series DB |
| **Need full-text search on reasoning** | Add PostgreSQL full-text index or Elasticsearch |
| **Need real-time streaming** | Add event sourcing layer, Kafka/Redis Streams |
| **Scale to billions of decisions** | Partition decisions table by date, add sharding |

