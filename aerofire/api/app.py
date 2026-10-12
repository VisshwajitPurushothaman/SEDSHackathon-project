"""FastAPI application.

Legacy pre-flight endpoints: GET /api/health, POST /api/simulate, GET /api/stream.
Simulation work runs in worker threads so the event loop never blocks (the
original SSE handler ran a blocking HTTP fetch on the loop).
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, AsyncIterator

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import load_config
from ..physics import atmosphere as A
from . import legacy
from .missions import router as missions_router

CFG = load_config()

app = FastAPI(title="AERO-FIRE Flight Director API", version="3.0")
# Local tool: any origin may read, no credentials (wildcard + credentials is not a valid CORS combination).
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


def current_profile() -> A.AtmosphereProfile:
    """Live Open-Meteo profile for the launch site (cached), or a labelled ISA fallback."""
    return A.fetch_profile(CFG, CFG.site.lat_deg, CFG.site.lon_deg)


app.include_router(missions_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "AERO-FIRE Backend Operational"}


class SimulationRequest(BaseModel):
    puncture_um: float = 0.0


@app.post("/api/simulate")
def simulate(req: SimulationRequest) -> dict[str, Any]:
    """Whole pre-flight run in one response (sync handler: FastAPI runs it in a worker thread)."""
    prof = current_profile()
    frames = list(legacy.execute_flight_engine(CFG, prof, req.puncture_um))
    return {"summary": legacy.summarize(frames, req.puncture_um, prof), "telemetry": frames}


def sse(data: Any, event: str | None = None, event_id: int | None = None) -> str:
    out = ""
    if event_id is not None:
        out += f"id: {event_id}\n"
    if event:
        out += f"event: {event}\n"
    return out + f"data: {json.dumps(data)}\n\n"


async def _thread_stream(gen_factory) -> AsyncIterator[Any]:
    """Run a blocking generator in a thread; yield its items on the event loop."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    done = object()

    def worker() -> None:
        try:
            for item in gen_factory():
                loop.call_soon_threadsafe(q.put_nowait, item)
        except Exception as exc:  # noqa: BLE001 - forwarded to the client as an error event
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, done)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = await q.get()
        if item is done:
            return
        yield item


@app.get("/api/stream")
async def stream(puncture_um: float = Query(0.0), pace_s: float = Query(0.1, ge=0.0, le=2.0)):
    """SSE: legacy frames with `id:` lines, then an explicit `event: end` so the client closes."""

    async def gen():
        prof = await asyncio.to_thread(current_profile)
        n = 0
        async for item in _thread_stream(lambda: legacy.execute_flight_engine(CFG, prof, puncture_um)):
            if isinstance(item, Exception):
                yield sse({"error": str(item)}, event="error")
                break
            n += 1
            yield sse(item, event_id=n)
            await asyncio.sleep(pace_s)
        yield sse({"frames": n, "atmo_source": prof.label}, event="end", event_id=n + 1)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
