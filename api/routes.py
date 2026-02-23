"""
Janus — REST API routes (all-in-one router).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from core.event_bus import event_bus
from core.inventory_service import list_drives, list_images, list_physical_ports, list_ports
from core.job_manager import job_manager
from core.layout_service import (
    export_layout_bytes,
    get_layout,
    import_layout,
    save_layout,
)
from core.models import BatchStartRequest, LayoutConfig

log = logging.getLogger("janus.api")

router = APIRouter(prefix="/api")


# ── Layout ───────────────────────────────────────────────────────────────────

@router.get("/layout", summary="Get current grid layout")
def api_get_layout():
    return get_layout().model_dump()


@router.put("/layout", summary="Save grid layout")
def api_put_layout(layout: LayoutConfig):
    save_layout(layout)
    return {"ok": True}


@router.post("/layout/import", summary="Import layout JSON")
async def api_import_layout(file: UploadFile = File(...)):
    try:
        raw = await file.read()
        layout = import_layout(raw)
        return layout.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/layout/export", summary="Export layout JSON")
def api_export_layout():
    data = export_layout_bytes()
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=layout.json"},
    )


# ── Inventory ────────────────────────────────────────────────────────────────

@router.get("/ports", summary="List available USB ports (flat)")
def api_list_ports():
    return list_ports()


@router.get("/ports/physical", summary="List physical USB ports with current device info")
def api_list_physical_ports():
    return list_physical_ports()


@router.get("/drives", summary="List connected drives")
def api_list_drives(removable: int = 0):
    return [d.model_dump() for d in list_drives(removable_only=bool(removable))]


@router.get("/images", summary="List available images")
def api_list_images():
    return [img.model_dump() for img in list_images()]


# ── Jobs & Batch ─────────────────────────────────────────────────────────────

@router.post("/batch/start", summary="Start batch flash")
async def api_batch_start(req: BatchStartRequest):
    jobs = await job_manager.start_batch(req)
    return [j.model_dump() for j in jobs]


@router.post("/batch/cancel", summary="Cancel all active jobs")
async def api_batch_cancel():
    await job_manager.cancel_all()
    return {"ok": True}


@router.post("/batch/retry", summary="Retry all failed jobs")
async def api_batch_retry():
    jobs = await job_manager.retry_all_failed()
    return [j.model_dump() for j in jobs]


@router.get("/jobs", summary="List all jobs")
def api_list_jobs():
    return [j.model_dump() for j in job_manager.list_jobs()]


@router.get("/jobs/{job_id}", summary="Get job details")
def api_get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump()


@router.post("/jobs/{job_id}/cancel", summary="Cancel a job")
async def api_cancel_job(job_id: str):
    ok = await job_manager.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"ok": True}


@router.post("/jobs/{job_id}/retry", summary="Retry a job")
async def api_retry_job(job_id: str):
    job = await job_manager.retry_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not in retryable state")
    return job.model_dump()


@router.post("/cells/{cell_id}/eject", summary="Eject device in cell")
async def api_eject_cell(cell_id: str):
    ok, msg = await job_manager.eject_cell(cell_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


import asyncio as _asyncio

# ── SSE Events ───────────────────────────────────────────────────────────────

@router.get("/events", summary="SSE event stream")
async def api_events():
    async def event_generator():
        subscriber = event_bus.subscribe()
        # Use a queue approach: wait for event or send heartbeat every 15s
        while True:
            try:
                # Wait up to 15 seconds for next event
                event_type, payload = await _asyncio.wait_for(
                    subscriber.__anext__(), timeout=15.0
                )
                yield f"event: {event_type}\ndata: {payload}\n\n"
            except _asyncio.TimeoutError:
                # Send SSE comment as keepalive
                yield ": heartbeat\n\n"
            except StopAsyncIteration:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
