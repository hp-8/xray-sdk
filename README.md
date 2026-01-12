# X-Ray SDK

Debug non-deterministic pipelines. Figure out *why* the system made a decision, not just what happened.

**Status**: ✅ Production-ready for demo scale (0 MyPy errors, comprehensive docs, tested)  
**Last Updated**: 2026-01-13

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Quick Start

```bash
# start the api
uvicorn api.main:app --reload --port 8000

# run the demo (new terminal)
python -m examples.competitor_selection
```

API docs: http://localhost:8000/docs

## Basic Usage

```python
from xray import XRay, Step, Decision

xray = XRay()

run_id = xray.start_run("competitor_selection", input={"product_id": "123"})

decisions = [
    Decision(candidate_id="prod-1", decision_type="rejected", reason="price_too_high"),
    Decision(candidate_id="prod-2", decision_type="accepted", score=0.85)
]

xray.record_step(run_id, Step(
    name="filtering",
    input={"count": 5000},
    output={"passed": 30},
    decisions=decisions,
    reasoning="Applied price and rating filters"
))

xray.complete_run(run_id, result={"winner": "prod-2"})
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./xray.db` | DB connection string |
| `XRAY_ENABLED` | `true` | Set to `false` to disable |
| `XRAY_SAMPLE_THRESHOLD` | `500` | Decisions before sampling kicks in |
| `XRAY_SAMPLE_PER_REASON` | `50` | Rejected decisions kept per reason |

## Project Layout

```
xray/           # SDK package
  client.py     # XRay client
  models.py     # Decision, Step models
  sampler.py    # Sampling logic

api/            # FastAPI server
  routes/
    ingest.py   # POST endpoints
    query.py    # GET/query endpoints
  db/
    models.py   # SQLAlchemy models

examples/
  competitor_selection.py
```

## API Endpoints

**Recording data:**
- `POST /v1/runs` - create run
- `POST /v1/runs/{id}/steps` - add step
- `PATCH /v1/runs/{id}` - complete run

**Querying:**
- `GET /v1/runs` - list runs
- `GET /v1/runs/{id}` - get run details
- `POST /v1/query/steps` - search steps (filter by rejection rate etc)
- `POST /v1/query/decisions` - search decisions (track candidate history)

## Core Ideas

**Decisions = events.** A candidate might get rejected in step 1, reconsidered in step 2, then accepted in step 3. We store each decision as an event to preserve that history.

**Sampling for scale.** 5000 filtering decisions -> keep all accepted + N rejected per reason. Stats computed *before* sampling so queries stay accurate.

**Pre-computed stats.** Each step stores `rejection_rate`, `rejection_reasons`, etc. Query "filtering steps with >90% rejection" without loading all decisions.

## Documentation

**Core Design**:
- `ARCHITECTURE.md` - System design, data model, API spec (9 pages)
- `docs/adr/ADR-001-xray-data-capture-and-sampling.md` - Sampling decision record
- `docs/prds/PRD-xray-lite-v1.md` - Product requirements

**Deep Dives** (`_prep/` folder):
- `interview.md` - Design decisions & rationale (36 pages)
- `SIMPLE_EXPLANATION.md` - Quick overview
- `VIDEO_SCRIPT.md` - Video walkthrough script
- `REQUIREMENTS_VERIFICATION.md` - Requirements coverage matrix
- `ACTUAL_IMPLEMENTATION_DATAFLOW.md` - Data flow diagrams
- `INFO_MD_REQUIREMENTS_CHECKLIST.md` - Line-by-line verification
- `FINAL_SUMMARY.md` - Complete implementation summary

## Code Quality

```bash
# Type safety
mypy --config-file mypy.ini api/ xray/
# Result: Success: no issues found in 16 source files ✅

# Run example
python -m examples.competitor_selection
# Result: Successfully records 5000 decisions, samples to ~530 ✅
```

## Recent Improvements (2026-01-13)

- ✅ Fixed all 9 MyPy errors (type-safe codebase)
- ✅ Implemented ADR-001 (server-side sampling with transparency)
- ✅ Extracted HTML template to separate module (separation of concerns)
- ✅ Centralized configuration in `xray/config.py`
- ✅ Removed duplicate code (DRY principle)
- ✅ Added input validation (DoS protection)
- ✅ Comprehensive documentation (3,500+ lines)

## Caveats

- Sync SDK (no background buffering) - suitable for demo, would add async for production
- SQLite by default - swap to Postgres for production
- No auth - CORS wide open (demo only)
- No unit tests - manual testing only
