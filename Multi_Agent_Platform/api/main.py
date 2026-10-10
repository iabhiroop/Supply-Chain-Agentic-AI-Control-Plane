"""Internal ERP / WMS / TMS API for the Multi-Agent Platform.

This is the data plane the agent tools call. It simulates the enterprise
backends (ERP, WMS, TMS, CRM) on top of the `logistics_scm` PostgreSQL
database, and is the only component permitted to mutate business data.

Runs on port 8001; the AI Gateway owns 8000.

    python -m api.main          # from Multi_Agent_Platform/
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.config.settings import settings
from api.database.db import fetch_one
from api.routers import customer, disruption, execution, inbound, inventory, memory, ops, platform, transport


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    async def _detector_loop() -> None:
        from agents.ops import detector

        await asyncio.sleep(1)
        while True:
            try:
                await asyncio.to_thread(detector.scan_and_upsert)
                await detector.maybe_auto_run_open_signals()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(max(10, settings.detector_interval_seconds))

    async def _partner_loop() -> None:
        from agents.execution.agentmail import agentmail_live, ensure_three_inboxes
        from agents.execution.poller import run_b2b_tick

        await asyncio.sleep(1)
        while True:
            try:
                if agentmail_live():
                    await asyncio.to_thread(ensure_three_inboxes)
                    await asyncio.to_thread(run_b2b_tick)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(max(2, settings.agentmail_poll_seconds))

    detector_task = asyncio.create_task(_detector_loop())
    partner_task = asyncio.create_task(_partner_loop())
    yield
    for task in (detector_task, partner_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Logistics SCM — System of Execution",
    description=(
        "Simulated ERP for purchase-intent-to-settlement. Postgres is the system of record. "
        "Four specialists (Sourcing, Procurement, Logistics, Finance) act through tools."
    ),
    version="2.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inventory.router)
app.include_router(transport.router)
app.include_router(customer.router)
app.include_router(inbound.router)
app.include_router(platform.router)
app.include_router(ops.router)
app.include_router(execution.api_router)
app.include_router(execution.ops_router)
app.include_router(disruption.router)
app.include_router(memory.router)


@app.get("/health", tags=["Meta"])
def health():
    """Liveness plus a real database round-trip, so a broken DSN fails loudly.

    The previous version of this API silently pointed at a non-existent SQLite
    file, which turned a configuration error into confusing empty tool results.
    """
    try:
        row = fetch_one("SELECT COUNT(*) AS orders FROM orders")
        return {
            "status": "healthy",
            "database": settings.pg_database,
            "orders": row["orders"] if row else 0,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unhealthy", "database": settings.pg_database, "error": str(exc)}


if __name__ == "__main__":
    import uvicorn

    print(f"Internal API on http://{settings.api_host}:{settings.api_port}  "
          f"(docs at /docs, database '{settings.pg_database}')")
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
