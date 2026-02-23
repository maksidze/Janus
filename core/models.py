"""
Janus — модели данных.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class JobState(str, Enum):
    QUEUED = "QUEUED"
    WRITING = "WRITING"
    VERIFYING = "VERIFYING"
    EXPANDING = "EXPANDING"
    RESIZING = "RESIZING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobStage(str, Enum):
    WRITE = "write"
    VERIFY = "verify"
    EXPAND = "expand"
    RESIZE = "resize"


class UsbHint(str, Enum):
    USB2 = "2.0"
    USB3 = "3.0"
    UNKNOWN = "unknown"


# ── Layout (grid cells) ─────────────────────────────────────────────────────

class PortCell(BaseModel):
    """One cell in the operator grid."""
    cell_id: str                          # e.g. "A1"
    label: str = ""                       # human alias
    port_id: str = ""                     # stable device path / by-path
    usb_hint: UsbHint = UsbHint.UNKNOWN
    enabled: bool = True


class LayoutConfig(BaseModel):
    schema_version: int = 1
    rows: int = 2
    cols: int = 4
    cell_size: str = "normal"             # "compact" | "normal"
    cells: List[PortCell] = Field(default_factory=list)


# ── Inventory ────────────────────────────────────────────────────────────────

class DriveInfo(BaseModel):
    device_path: str                      # /dev/sdX
    by_path: str = ""                     # /dev/disk/by-path/...
    model: str = ""
    serial: str = ""
    vendor: str = ""
    size_bytes: int = 0
    size_human: str = ""
    removable: bool = False
    mounted: bool = False
    mountpoints: List[str] = Field(default_factory=list)
    usb_speed: str = ""
    port_path: str = ""                   # usb topology hint
    is_system: bool = False               # contains root partition


class ImageInfo(BaseModel):
    name: str
    path: str
    size_bytes: int = 0
    size_human: str = ""
    mtime: float = 0.0
    img_type: str = ""                    # img / img.xz / img.gz / iso


# ── Job / Batch ──────────────────────────────────────────────────────────────

class BatchOptions(BaseModel):
    verify: bool = False
    expand_partition: bool = False
    resize_filesystem: bool = False
    eject_after_done: bool = False


class BatchStartRequest(BaseModel):
    image_name: str
    cell_ids: List[str]
    options: BatchOptions = Field(default_factory=BatchOptions)
    concurrency: int = 1


class JobInfo(BaseModel):
    job_id: str
    cell_id: str
    device_path: str = ""
    image_name: str = ""
    state: JobState = JobState.QUEUED
    stage: JobStage = JobStage.WRITE
    progress: float = 0.0                 # 0..1
    speed_bytes: float = 0.0
    speed_human: str = ""
    eta_sec: float = 0.0
    eta_human: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    log_tail: List[str] = Field(default_factory=list)
    warning: Optional[str] = None


class BatchInfo(BaseModel):
    batch_id: str
    image_name: str
    options: BatchOptions = Field(default_factory=BatchOptions)
    concurrency: int = 1
    cell_ids: List[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
