# Data Structure Decisions

This doc explains **why** each data structure was chosen, what alternatives were considered, and what the trade-offs are.

---

## Decisions: Why a List?

```python
class Step(BaseModel):
    decisions: list[Decision] | None = None
```

### The key insight

Decisions are **time-ordered events**, not a lookup table.

```python
# What we need to express:
decisions = [
    Decision(candidate_id="A", decision_type="rejected", reason="price_too_high"),   # t=1
    Decision(candidate_id="B", decision_type="accepted", score=0.9),                  # t=2
    Decision(candidate_id="A", decision_type="accepted", reason="price_dropped"),    # t=3 (reconsidered!)
]

# A dictionary would lose this:
# decisions_dict = {"A": ???, "B": ???}  # Which decision for A?
```

### Why list works

- **Order preserved** - decisions happen in sequence, that matters for debugging
- **Duplicates allowed** - same candidate can be evaluated multiple times
- **Easy iteration** - we iterate through all decisions for stats and sampling
- **JSON-friendly** - arrays map directly to JSON

### Alternatives

| Alternative | Problem |
|-------------|---------|
| `dict[candidate_id, Decision]` | Loses order. One candidate = one decision only. |
| `set[Decision]` | No order, no duplicates, requires hashing. |
| `dict[candidate_id, list[Decision]]` | Adds complexity. Order across candidates is lost. |

---

## Sampling Algorithm

### What we do

```python
def sample(self, decisions: list[Decision]) -> tuple[list[Decision], bool]:
    if len(decisions) <= self.threshold:  # default 500
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
    
    # Sample N (default 50) per reason
    sampled_rejected = []
    for reason, items in rejected_by_reason.items():
        if len(items) <= self.per_reason:
            sampled_rejected.extend(items)
        else:
            sampled_rejected.extend(random.sample(items, self.per_reason))
    
    return accepted + sampled_rejected + pending, True
```

### Why this works

| Choice | Reasoning |
|--------|-----------|
| Keep ALL accepted | Winners matter most. "Why did this pass?" is a common question. |
| N per rejection reason | Preserves reasoning diversity. If 2000 rejected for price and 500 for rating, both reasons are represented. |
| Random within reason | Simple, unbiased. |
| Stats computed before sampling | Queries see accurate counts, not sampled counts. |

### What's good

- Reasoning diversity is preserved
- Simple to understand
- Configurable (`threshold`, `per_reason`)
- Stats stay accurate

### What's not great

| Issue | Impact |
|-------|--------|
| **O(n) memory** | Must load ALL decisions before sampling. 5000 = fine. 5M = OOM. |
| **Random loses time distribution** | Early vs late rejections might differ. |
| **Unbounded output** | 20 reasons × 50 per_reason = 1000. Can exceed threshold. |
| **No importance weighting** | Borderline cases (score=0.49) treated same as clear rejections (score=0.1). |

### Alternatives considered

**Reservoir sampling** - O(k) memory, great for streaming. But loses reason-based diversity.

**Head + tail + random** - Preserves temporal boundaries ("first failure", "last failure"). But loses reason diversity.

**Stratified proportional** - Statistically representative. But rare reasons (which might be bugs!) could be lost.

**Importance sampling** - Keeps borderline cases. But requires score field and might drop clear rejections.

### Production version would look like

```python
class ProductionSampler:
    """
    Combines:
    1. All accepted
    2. Head + tail per reason (temporal boundaries)
    3. Importance-weighted middle (edge cases)
    4. Hard cap on total
    """
    
    def sample(self, decisions):
        # ... implementation ...
```

### For the 5000→30 problem

Our simple approach is fine:

| Scale | Memory | Works? |
|-------|--------|--------|
| 5,000 | ~500KB | ✅ |
| 50,000 | ~5MB | ✅ |
| 500,000 | ~50MB | ⚠️ |
| 5,000,000 | ~500MB | ❌ needs streaming |

---

## Stats: Why a Dict?

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

### Why

- **Flexible schema** - filtering step has rejection_reasons, ranking step might have score_distribution
- **Direct DB storage** - JSON columns in PostgreSQL/SQLite
- **API-friendly** - serializes directly
- **Queryable** - PostgreSQL can index JSONB

### Alternatives

| Alternative | Problem |
|-------------|---------|
| Fixed `StepStats` class | Can't add new stats without schema change. |
| Separate key-value table | Over-normalized. Simple stats become multiple rows. |
| Flatten into Step columns | Fixed schema. Unused columns everywhere. |

We also have a Pydantic model for validation:

```python
class StepStats(BaseModel):
    input_count: int = 0
    output_count: int = 0
    rejection_rate: float = 0.0
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
```

Best of both: validate in SDK, store flexibly in DB.

---

## Input/Output/Config: Also Dicts

```python
input: dict[str, Any] | None   # {"candidate_count": 5000, "keywords": ["laptop"]}
output: dict[str, Any] | None  # {"passed_count": 30, "winner_id": "prod-123"}
config: dict[str, Any] | None  # {"price_threshold": 100, "min_rating": 3.5}
```

### Why

- **Domain-agnostic** - X-Ray doesn't know your business. Competitor selection has different inputs than content generation.
- **Extensible** - add fields without migrations
- **Self-documenting** - keys describe the data

Typed models would make X-Ray domain-specific. Every new pipeline type would need new models.

---

## Database: Relational + JSON Hybrid

```
runs
├── id (PK)
├── pipeline_type (indexed)
├── status (indexed)
├── input_context (JSON)
├── output_result (JSON)
└── metadata (JSON)

steps
├── id (PK)
├── run_id (FK, indexed)
├── step_name (indexed)
├── sequence_order
├── input_data (JSON)
├── output_data (JSON)
├── config (JSON)
├── stats (JSON)
└── reasoning

decisions
├── id (PK)
├── step_id (FK, indexed)
├── candidate_id (indexed)
├── decision_type (indexed)
├── reason (indexed)
├── score
├── sequence_order
└── metadata (JSON)
```

### Why hybrid

| Field type | Storage | Why |
|------------|---------|-----|
| IDs, FKs | Relational | Joins, referential integrity |
| Queryable fields | Relational + index | Fast WHERE |
| Flexible data | JSON | Schema flexibility |

### Why decisions are a separate table

Could have embedded them as JSON in Step:

```python
step.decisions = JSON([{...}, {...}, ...])
```

But then this query is impossible:

```sql
SELECT * FROM decisions WHERE candidate_id = 'prod-123';
-- Works across ALL steps and runs!
```

We need cross-run queries on decisions.

---

## Evidence: Separate Table

```sql
evidence
├── id
├── decision_id (FK)
├── evidence_type
└── data (JSON)
```

Why separate:
- LLM outputs are large. Keeping decisions light.
- Not every decision has evidence. Embedding wastes space.
- Can query by type: "show all LLM evidence"
- Lazy loading: fetch decisions first, evidence on demand

---

## Sequence Order: Integer

```python
for idx, decision in enumerate(decisions):
    Decision(..., sequence_order=idx)
```

Why integer over timestamp:
- Deterministic ordering (no ties at same millisecond)
- Meaningful ("3rd decision" vs "2024-01-01T12:00:00.123Z")
- Space efficient
- No clock skew issues

We also store `created_at` for when, but `sequence_order` for order within the step.

---

## IDs: UUID Strings

```python
id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
```

Why:
- Globally unique across distributed systems
- Client can generate before API call (useful for async buffering)
- No coordination needed
- URL-safe

---

## Summary

| Component | Structure | Why |
|-----------|-----------|-----|
| Decisions | `list[Decision]` | Order matters, duplicates allowed |
| Grouping | `dict[str, list]` | O(1) lookup for "N per reason" |
| Stats | JSON dict | Flexible schema |
| Input/Output | JSON dict | Domain agnostic |
| Decision storage | Separate SQL table | Cross-run queries |
| Evidence | Separate table | Heavy payloads, lazy loading |
| Sequence | Integer | Deterministic, no ties |
| IDs | UUID strings | Client-generatable |

---

## When would we change this?

| Scenario | Change |
|----------|--------|
| Need type safety on inputs | Add Pydantic schemas per pipeline_type |
| Need time-series queries | Timestamp indexing, time-series DB |
| Need full-text search | PostgreSQL full-text or Elasticsearch |
| Need real-time streaming | Event sourcing, Kafka |
| Billions of decisions | Partition by date, sharding |
