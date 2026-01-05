# X-Ray SDK & API

Debug non-deterministic, multi-step algorithmic systems.

X-Ray provides transparency into multi-step decision processes by capturing inputs, candidates, filters, outcomes, and **reasoning** at each step. Unlike traditional tracing which answers "what happened?", X-Ray answers "**why did the system make this decision?**"

## Requirements

- **Python 3.11+**
- SQLite (default, no setup needed) or PostgreSQL

## Quick Start

### 1. Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Setup Environment Variables (Optional)

```bash
# Copy example env file
cp env.example .env

# Edit .env if needed (defaults work for local development)
```

### 4. Start the API Server

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for the interactive API documentation.

### 5. Run the Demo

```bash
# Basic demo (uses mock data)
python -m examples.competitor_selection

# Amazon competitor selection demo (uses real LLM if OPENAI_API_KEY is set)
python -m examples.amazon_competitor_selection
```

The basic demo runs a simulated competitor selection pipeline that:
1. Generates search keywords
2. Searches for 5000 candidate products
3. Filters by price, rating, and category
4. Ranks and selects the best match

### 6. Query the Results

```bash
# List all runs
curl http://localhost:8000/v1/runs

# Get a specific run with all steps
curl http://localhost:8000/v1/runs/{run_id}

# Query filtering steps with high rejection rate
curl -X POST http://localhost:8000/v1/query/steps \
  -H "Content-Type: application/json" \
  -d '{"step_name": "filtering", "min_rejection_rate": 0.9}'
```

## SDK Usage

```python
from xray import XRay, Step, Decision

# Initialize the client
xray = XRay(api_url="http://localhost:8000")

# Start a pipeline run
run_id = xray.start_run(
    pipeline_type="competitor_selection",
    input={"product_id": "123", "title": "Laptop Stand"}
)

# Record a filtering step with decisions
decisions = []
for candidate in candidates:
    if candidate["price"] > 100:
        decisions.append(Decision(
            candidate_id=candidate["id"],
            decision_type="rejected",
            reason="price_exceeds_threshold",
            metadata={"price": candidate["price"]}
        ))
    else:
        decisions.append(Decision(
            candidate_id=candidate["id"],
            decision_type="accepted",
            score=candidate["relevance_score"]
        ))

xray.record_step(run_id, Step(
    name="filtering",
    input={"candidate_count": len(candidates)},
    output={"passed_count": sum(1 for d in decisions if d.decision_type == "accepted")},
    decisions=decisions,
    reasoning="Applied price cap ($100)"
))

# Complete the run
xray.complete_run(run_id, result={"winner_id": "product-456"})
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./xray.db` | Database connection string |
| `XRAY_API_URL` | `http://localhost:8000` | API URL for SDK |
| `XRAY_ENABLED` | `true` | Enable/disable SDK recording |
| `XRAY_SAMPLE_THRESHOLD` | `500` | Max decisions before sampling |
| `XRAY_SAMPLE_PER_REASON` | `50` | Rejected decisions to keep per reason |

## Project Structure

```
.
├── xray/                    # SDK package
│   ├── __init__.py          # Exports: XRay, Step, Decision, Evidence
│   ├── client.py            # XRay client class
│   ├── models.py            # Pydantic models
│   └── sampler.py           # Decision sampling logic
├── api/                     # API server
│   ├── main.py              # FastAPI app
│   ├── routes/
│   │   ├── ingest.py        # POST endpoints for recording
│   │   ├── query.py         # GET/POST endpoints for querying
│   │   └── visualize.py     # HTML visualization endpoint
│   └── db/
│       ├── database.py      # SQLAlchemy setup
│       └── models.py        # ORM models
├── examples/
│   ├── competitor_selection.py           # Basic demo
│   └── amazon_competitor_selection.py    # Full scenario from assignment
├── ARCHITECTURE.md          # Design rationale
├── README.md
└── requirements.txt
```

## API Endpoints

### Ingest

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/runs` | Create a new run |
| POST | `/v1/runs/{run_id}/steps` | Record a step with decisions |
| PATCH | `/v1/runs/{run_id}` | Complete a run |

### Query

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/runs` | List runs with filters |
| GET | `/v1/runs/{run_id}` | Get run with all steps |
| GET | `/v1/runs/{run_id}/steps/{step_id}/decisions` | Get decisions for a step |
| POST | `/v1/query/steps` | Query steps across runs |
| POST | `/v1/query/decisions` | Query decisions across steps |

### Visualization

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/visualize/runs/{run_id}` | HTML visualization of a run |

## Key Concepts

### Decisions as Events

Unlike systems that track candidates as entities, X-Ray models **decisions as time-ordered events**. This enables:

- Tracking a candidate through multiple steps (rejected → reconsidered → accepted)
- Sampling that preserves reasoning diversity
- Debugging the decision timeline

### Sampling Strategy

When a step has many decisions (e.g., 5000 candidates filtered to 30), the SDK samples:

1. **All accepted decisions** (preserve what passed)
2. **N rejected per reason** (preserve why things failed)
3. **Pre-compute stats** (enable efficient queries)

### Stats for Queryability

Each step stores pre-computed stats:
- `input_count`: Total candidates evaluated
- `output_count`: Accepted decisions
- `rejection_rate`: Percentage rejected
- `rejection_reasons`: Count per reason

This enables queries like "all filtering steps with >90% rejection rate" without scanning decision tables.

## Known Limitations

- **Synchronous SDK**: Steps are sent synchronously. For high-throughput pipelines, consider batching.
- **SQLite limitations**: For production, use PostgreSQL
- **No authentication**: Add auth middleware for production use

## Future Improvements

- Web UI for exploring runs and decisions
- Async buffering in SDK for better performance
- OpenTelemetry integration
- Multi-language SDKs (JavaScript, Go)
- Anomaly detection for unusual rejection patterns
