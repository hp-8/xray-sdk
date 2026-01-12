import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db.database import init_db, close_db
from api.routes import ingest, query, visualize


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="X-Ray API",
    description="Debug non-deterministic, multi-step algorithmic systems",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    # ⚠️ SECURITY WARNING: Demo-only configuration!
    # TODO: For production deployment:
    #   1. Replace allow_origins=["*"] with specific domain whitelist
    #   2. Implement authentication/authorization (JWT, OAuth, API keys)
    #   3. Add rate limiting middleware
    #   4. Enable HTTPS-only in production
    # PRD Reference: Section 8 (Non-Functional Requirements - Security)
    allow_origins=["*"],  # INSECURE - replace in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(query.router)
app.include_router(visualize.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "X-Ray API", "version": "0.1.0"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("API_HOST", "0.0.0.0"), port=int(os.getenv("API_PORT", "8000")))
