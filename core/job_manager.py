"""
Janus — Job manager with async concurrency control.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Dict, List, Optional

from core.event_bus import event_bus
from core.flash_runner import (
    expand_partition,
    resize_filesystem,
    verify_image,
    write_image,
)
from core.inventory_service import eject_device, list_drives, unmount_device
from core.layout_service import get_layout
from core.models import (
    BatchInfo,
    BatchOptions,
    BatchStartRequest,
    JobInfo,
    JobStage,
    JobState,
)

log = logging.getLogger("janus.jobs")


class JobManager:
    """Manages flash jobs with concurrency limiting."""

    def __init__(self):
        self._jobs: Dict[str, JobInfo] = {}
        self._batches: Dict[str, BatchInfo] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._cancel_flags: Dict[str, bool] = {}
        self._kill_events: Dict[str, threading.Event] = {}

    # ── Public API ───────────────────────────────────────────────────────

    def list_jobs(self) -> List[JobInfo]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> Optional[JobInfo]:
        return self._jobs.get(job_id)

    async def start_batch(self, req: BatchStartRequest) -> List[JobInfo]:
        """Create jobs for selected cells and start them with concurrency."""
        layout = get_layout()
        cell_map = {c.cell_id: c for c in layout.cells}
        drives = list_drives()
        drive_by_path = {}
        for d in drives:
            drive_by_path[d.device_path] = d
            if d.by_path:
                drive_by_path[d.by_path] = d

        batch_id = str(uuid.uuid4())
        batch = BatchInfo(
            batch_id=batch_id,
            image_name=req.image_name,
            options=req.options,
            concurrency=req.concurrency,
            cell_ids=req.cell_ids,
        )
        self._batches[batch_id] = batch

        self._semaphore = asyncio.Semaphore(max(1, req.concurrency))

        created_jobs: List[JobInfo] = []
        for cell_id in req.cell_ids:
            cell = cell_map.get(cell_id)
            if not cell or not cell.enabled:
                continue

            # Resolve device from port_id
            drive = drive_by_path.get(cell.port_id)
            if not drive:
                # Try to find by device_path directly
                drive = drive_by_path.get(cell.port_id)

            device_path = drive.device_path if drive else cell.port_id

            job_id = str(uuid.uuid4())
            job = JobInfo(
                job_id=job_id,
                cell_id=cell_id,
                device_path=device_path,
                image_name=req.image_name,
            )

            # Safety checks
            error = self._safety_check(drive, device_path)
            if error:
                job.state = JobState.FAILED
                job.error = error
                self._jobs[job_id] = job
                created_jobs.append(job)
                await self._publish_update(job)
                continue

            self._jobs[job_id] = job
            self._cancel_flags[job_id] = False
            self._kill_events[job_id] = threading.Event()
            created_jobs.append(job)

            task = asyncio.create_task(
                self._run_job(job, req.options)
            )
            self._tasks[job_id] = task

        return created_jobs

    def _safety_check(self, drive: Optional[object], device_path: str) -> Optional[str]:
        """Return error string if device is unsafe to write to."""
        if not device_path or device_path == "":
            return "No device bound to this cell"
        if drive is None:
            return f"Device {device_path} not found / not connected"
        d = drive  # type: ignore
        if d.is_system:
            return f"BLOCKED: {device_path} contains system/root partition"
        if not d.removable:
            return f"BLOCKED: {device_path} is not removable"
        return None

    async def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
            return False
        self._cancel_flags[job_id] = True
        # Signal the worker thread to kill the subprocess immediately
        kill_ev = self._kill_events.get(job_id)
        if kill_ev:
            kill_ev.set()
        job.state = JobState.CANCELLED
        job.finished_at = time.time()
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        await self._publish_update(job)
        return True

    async def retry_job(self, job_id: str) -> Optional[JobInfo]:
        old = self._jobs.get(job_id)
        if not old:
            return None
        if old.state not in (JobState.FAILED, JobState.CANCELLED):
            return None

        # Create a new job for the same cell
        new_id = str(uuid.uuid4())
        job = JobInfo(
            job_id=new_id,
            cell_id=old.cell_id,
            device_path=old.device_path,
            image_name=old.image_name,
        )
        # Re-check safety
        drives = list_drives()
        drive = None
        for d in drives:
            if d.device_path == old.device_path:
                drive = d
                break
        error = self._safety_check(drive, old.device_path)
        if error:
            job.state = JobState.FAILED
            job.error = error
            self._jobs[new_id] = job
            await self._publish_update(job)
            return job

        self._jobs[new_id] = job
        self._cancel_flags[new_id] = False
        self._kill_events[new_id] = threading.Event()
        # Remove old job
        self._jobs.pop(job_id, None)

        # Reconstruct options from batch or use defaults
        options = BatchOptions()
        for b in self._batches.values():
            if old.cell_id in b.cell_ids:
                options = b.options
                break

        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(2)

        task = asyncio.create_task(self._run_job(job, options))
        self._tasks[new_id] = task
        return job

    async def eject_cell(self, cell_id: str) -> tuple[bool, str]:
        layout = get_layout()
        cell = None
        for c in layout.cells:
            if c.cell_id == cell_id:
                cell = c
                break
        if not cell or not cell.port_id:
            return False, "Cell not found or no device bound"

        drives = list_drives()
        dev = None
        for d in drives:
            if d.device_path == cell.port_id or d.by_path == cell.port_id:
                dev = d
                break
        if not dev:
            return False, "Device not connected"

        return eject_device(dev.device_path)

    async def cancel_all(self):
        for job_id, job in list(self._jobs.items()):
            if job.state in (JobState.QUEUED, JobState.WRITING, JobState.VERIFYING,
                             JobState.EXPANDING, JobState.RESIZING):
                await self.cancel_job(job_id)

    async def retry_all_failed(self) -> List[JobInfo]:
        retried = []
        for job_id, job in list(self._jobs.items()):
            if job.state == JobState.FAILED:
                new_job = await self.retry_job(job_id)
                if new_job:
                    retried.append(new_job)
        return retried

    # ── Internal ─────────────────────────────────────────────────────────

    async def _run_job(self, job: JobInfo, options: BatchOptions):
        """Execute the full flash pipeline for one job."""
        assert self._semaphore is not None
        async with self._semaphore:
            if self._cancel_flags.get(job.job_id):
                return
            await self._execute_pipeline(job, options)

    async def _execute_pipeline(self, job: JobInfo, options: BatchOptions):
        """Run write → verify → expand → resize pipeline in a thread."""
        from core.inventory_service import list_images

        job.started_at = time.time()
        log_lines = job.log_tail

        # Resolve image path
        images = list_images()
        image_path = None
        for img in images:
            if img.name == job.image_name:
                image_path = img.path
                break
        if not image_path:
            job.state = JobState.FAILED
            job.error = f"Image '{job.image_name}' not found"
            job.finished_at = time.time()
            await self._publish_update(job)
            return

        device = job.device_path

        # Unmount before writing
        ok, msg = await asyncio.to_thread(unmount_device, device)
        if not ok:
            log_lines.append(f"WARN: unmount: {msg}")

        # Capture the running event loop once, before entering any thread
        loop = asyncio.get_running_loop()
        kill_event = self._kill_events.get(job.job_id)

        def make_update_cb(stage: JobStage):
            def cb(fields: dict):
                job.stage = stage
                for k, v in fields.items():
                    if hasattr(job, k):
                        setattr(job, k, v)
                # Schedule publish on the main event loop from the worker thread
                try:
                    asyncio.run_coroutine_threadsafe(self._publish_update(job), loop)
                except Exception:
                    pass
            return cb

        # ── WRITE ────────────────────────────────────────────────────
        job.state = JobState.WRITING
        job.stage = JobStage.WRITE
        job.progress = 0.0
        await self._publish_update(job)

        success = await asyncio.to_thread(
            write_image, image_path, device,
            make_update_cb(JobStage.WRITE), log_lines, kill_event
        )
        if self._cancel_flags.get(job.job_id) or (kill_event and kill_event.is_set()):
            job.state = JobState.CANCELLED
            job.finished_at = time.time()
            await self._publish_update(job)
            return
        if not success:
            job.state = JobState.FAILED
            job.error = "Write failed"
            job.finished_at = time.time()
            await self._publish_update(job)
            return

        # ── VERIFY ───────────────────────────────────────────────────
        if options.verify:
            job.state = JobState.VERIFYING
            job.stage = JobStage.VERIFY
            job.progress = 0.0
            await self._publish_update(job)

            success = await asyncio.to_thread(
                verify_image, image_path, device,
                make_update_cb(JobStage.VERIFY), log_lines, kill_event
            )
            if self._cancel_flags.get(job.job_id) or (kill_event and kill_event.is_set()):
                job.state = JobState.CANCELLED
                job.finished_at = time.time()
                await self._publish_update(job)
                return
            if not success:
                job.state = JobState.FAILED
                job.error = "Verification failed"
                job.finished_at = time.time()
                await self._publish_update(job)
                return

        # ── EXPAND ───────────────────────────────────────────────────
        if options.expand_partition:
            if kill_event and kill_event.is_set():
                job.state = JobState.CANCELLED
                job.finished_at = time.time()
                await self._publish_update(job)
                return
            job.state = JobState.EXPANDING
            job.stage = JobStage.EXPAND
            job.progress = 0.0
            await self._publish_update(job)

            success = await asyncio.to_thread(
                expand_partition, device,
                make_update_cb(JobStage.EXPAND), log_lines, kill_event
            )
            if not success:
                job.warning = "Expand partition failed (non-fatal)"
                log_lines.append("WARN: expand failed, continuing")

        # ── RESIZE ───────────────────────────────────────────────────
        if options.resize_filesystem:
            if kill_event and kill_event.is_set():
                job.state = JobState.CANCELLED
                job.finished_at = time.time()
                await self._publish_update(job)
                return
            job.state = JobState.RESIZING
            job.stage = JobStage.RESIZE
            job.progress = 0.0
            await self._publish_update(job)

            success = await asyncio.to_thread(
                resize_filesystem, device,
                make_update_cb(JobStage.RESIZE), log_lines, kill_event
            )
            if not success:
                job.warning = (job.warning or "") + "; Resize failed (non-fatal)"
                log_lines.append("WARN: resize failed, continuing")

        # ── DONE ─────────────────────────────────────────────────────
        job.state = JobState.DONE
        job.progress = 1.0
        job.finished_at = time.time()
        await self._publish_update(job)

        # ── EJECT ────────────────────────────────────────────────────
        if options.eject_after_done:
            ok, msg = await asyncio.to_thread(eject_device, device)
            if ok:
                log_lines.append("Ejected successfully")
            else:
                log_lines.append(f"WARN: eject: {msg}")
            await self._publish_update(job)

    async def _publish_update(self, job: JobInfo):
        await event_bus.publish("job_update", job.model_dump())


# Global singleton
job_manager = JobManager()

