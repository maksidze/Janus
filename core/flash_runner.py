"""
Janus — Flash runner: dd-based writing pipeline with optional verify / expand / resize.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("janus.flash")

UpdateCb = Callable[[dict], None]  # on_update(fields)


def _human_speed(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 ** 2:
        return f"{bps / 1024:.1f} KB/s"
    elif bps < 1024 ** 3:
        return f"{bps / 1024 ** 2:.1f} MB/s"
    return f"{bps / 1024 ** 3:.2f} GB/s"


def _human_eta(secs: float) -> str:
    if secs <= 0:
        return "--:--"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _image_size(image_path: str) -> int:
    """Return uncompressed image size (or file size for raw .img)."""
    return os.path.getsize(image_path)


def _device_size(device: str) -> int:
    try:
        out = subprocess.check_output(
            ["blockdev", "--getsize64", device], text=True, timeout=5
        ).strip()
        return int(out)
    except Exception:
        return 0


# ── Write stage ──────────────────────────────────────────────────────────────

def write_image(image_path: str, device: str, on_update: UpdateCb,
                log_lines: list[str],
                kill_event: Optional[threading.Event] = None) -> bool:
    """
    Write image to device using dd.  Parses dd's status=progress output.
    Returns True on success.  Kills dd immediately if kill_event is set.
    """
    img_size = _image_size(image_path)
    bs = "4M"

    # Determine if we need decompression
    if image_path.endswith(".xz"):
        cmd = f"xzcat '{image_path}' | dd of='{device}' bs={bs} conv=fsync status=progress"
    elif image_path.endswith(".gz"):
        cmd = f"gunzip -c '{image_path}' | dd of='{device}' bs={bs} conv=fsync status=progress"
    elif image_path.endswith(".bz2"):
        cmd = f"bzcat '{image_path}' | dd of='{device}' bs={bs} conv=fsync status=progress"
    elif image_path.endswith(".zst"):
        cmd = f"zstdcat '{image_path}' | dd of='{device}' bs={bs} conv=fsync status=progress"
    else:
        cmd = f"dd if='{image_path}' of='{device}' bs={bs} conv=fsync status=progress"
        # For raw images we know the exact size
        img_size = os.path.getsize(image_path)

    log.info("write cmd: %s", cmd)
    log_lines.append(f"$ {cmd}")

    proc = subprocess.Popen(
        cmd, shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # dd writes progress to stderr
    start = time.time()
    buf = ""
    RE_BYTES = re.compile(r"(\d[\d\s]*)\s+bytes?\b.*copied", re.IGNORECASE)

    cancelled = False
    while True:
        # Check kill flag before each read
        if kill_event and kill_event.is_set():
            log_lines.append("CANCELLED: killing dd process")
            try:
                proc.kill()
            except Exception:
                pass
            cancelled = True
            break

        # Non-blocking read with a short poll so we can check kill_event
        import select
        rlist, _, _ = select.select([proc.stderr], [], [], 0.2)
        if not rlist:
            # No data yet — check if process finished
            if proc.poll() is not None:
                break
            continue

        ch = proc.stderr.read(1)
        if not ch:
            break
        if ch in ("\r", "\n"):
            line = buf.strip()
            buf = ""
            if not line:
                continue
            log_lines.append(line)
            if len(log_lines) > 200:
                log_lines.pop(0)
            m = RE_BYTES.search(line)
            if m and img_size > 0:
                copied = int(m.group(1).replace(" ", ""))
                progress = min(copied / img_size, 1.0)
                elapsed = time.time() - start
                speed = copied / elapsed if elapsed > 0 else 0
                eta = (img_size - copied) / speed if speed > 0 else 0
                on_update({
                    "progress": round(progress, 4),
                    "speed_bytes": speed,
                    "speed_human": _human_speed(speed),
                    "eta_sec": round(eta, 1),
                    "eta_human": _human_eta(eta),
                })
        else:
            buf += ch

    proc.wait()

    if cancelled:
        return False

    if proc.returncode != 0:
        err = buf.strip() or f"dd exited with code {proc.returncode}"
        log_lines.append(f"ERROR: {err}")
        return False

    # Sync
    subprocess.run(["sync"], timeout=30)
    on_update({"progress": 1.0, "speed_human": "--", "eta_human": "done"})
    return True


# ── Verify stage ─────────────────────────────────────────────────────────────

def verify_image(image_path: str, device: str, on_update: UpdateCb,
                 log_lines: list[str],
                 kill_event: Optional[threading.Event] = None) -> bool:
    """Compare sha256 of image vs written data on device."""
    img_size = os.path.getsize(image_path)
    if img_size == 0:
        log_lines.append("WARN: image size is 0, skipping verify")
        return True

    log_lines.append("Verifying: computing SHA-256 of image …")
    on_update({"progress": 0.0})

    sha_img = hashlib.sha256()
    read_so_far = 0
    with open(image_path, "rb") as f:
        while True:
            if kill_event and kill_event.is_set():
                log_lines.append("CANCELLED during verify")
                return False
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            sha_img.update(chunk)
            read_so_far += len(chunk)
            on_update({"progress": round(read_so_far / (img_size * 2), 4)})

    hex_img = sha_img.hexdigest()
    log_lines.append(f"Image SHA-256: {hex_img}")

    log_lines.append("Verifying: computing SHA-256 of device …")
    sha_dev = hashlib.sha256()
    read_so_far = 0
    with open(device, "rb") as f:
        while True:
            if kill_event and kill_event.is_set():
                log_lines.append("CANCELLED during verify (device read)")
                return False
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            sha_dev.update(chunk)
            read_so_far += len(chunk)
            on_update({"progress": round(0.5 + read_so_far / (img_size * 2), 4)})
            if read_so_far >= img_size:
                break

    hex_dev = sha_dev.hexdigest()
    log_lines.append(f"Device SHA-256: {hex_dev}")

    if hex_img == hex_dev:
        log_lines.append("Verify OK ✓")
        on_update({"progress": 1.0})
        return True
    else:
        log_lines.append("Verify FAILED ✗ — checksums do not match!")
        return False


# ── Expand partition ─────────────────────────────────────────────────────────

def expand_partition(device: str, on_update: UpdateCb,
                     log_lines: list[str],
                     kill_event: Optional[threading.Event] = None) -> bool:
    """Run growpart on the last partition of the device."""
    if kill_event and kill_event.is_set():
        return False
    on_update({"progress": 0.0})
    try:
        # Find last partition number
        raw = subprocess.check_output(
            ["lsblk", "-J", "-n", "-o", "NAME,TYPE", device],
            text=True, timeout=10,
        )
        import json
        data = json.loads(raw)
        parts = []
        for bd in data.get("blockdevices", []):
            for ch in (bd.get("children") or []):
                if ch.get("type") == "part":
                    parts.append(ch["name"])
        if not parts:
            log_lines.append("WARN: no partitions found, skipping expand")
            on_update({"progress": 1.0})
            return True

        last = parts[-1]
        # Extract partition number
        m = re.search(r"(\d+)$", last)
        part_num = m.group(1) if m else "1"

        cmd = ["growpart", device, part_num]
        log_lines.append(f"$ {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        log_lines.append(r.stdout.strip())
        if r.stderr.strip():
            log_lines.append(r.stderr.strip())
        on_update({"progress": 1.0})
        if r.returncode not in (0, 1):  # 1 = NOCHANGE (already expanded)
            return False
        return True
    except FileNotFoundError:
        log_lines.append("WARN: growpart not found, skipping expand")
        on_update({"progress": 1.0})
        return True
    except Exception as exc:
        log_lines.append(f"ERROR expand: {exc}")
        on_update({"progress": 1.0})
        return False


# ── Resize filesystem ────────────────────────────────────────────────────────

def resize_filesystem(device: str, on_update: UpdateCb,
                      log_lines: list[str],
                      kill_event: Optional[threading.Event] = None) -> bool:
    """Run resize2fs on the last partition (if ext2/3/4)."""
    if kill_event and kill_event.is_set():
        return False
    on_update({"progress": 0.0})
    try:
        raw = subprocess.check_output(
            ["lsblk", "-J", "-n", "-o", "NAME,FSTYPE,TYPE", device],
            text=True, timeout=10,
        )
        import json
        data = json.loads(raw)
        last_part = None
        last_fs = None
        for bd in data.get("blockdevices", []):
            for ch in (bd.get("children") or []):
                if ch.get("type") == "part":
                    last_part = ch["name"]
                    last_fs = ch.get("fstype") or ""

        if not last_part:
            log_lines.append("WARN: no partitions found, skipping resize")
            on_update({"progress": 1.0})
            return True

        if last_fs not in ("ext2", "ext3", "ext4"):
            log_lines.append(f"WARN: filesystem is {last_fs}, resize2fs only works with ext*, skipping")
            on_update({"progress": 1.0})
            return True

        part_dev = f"/dev/{last_part}"
        # e2fsck first
        subprocess.run(["e2fsck", "-f", "-y", part_dev],
                        capture_output=True, timeout=120)

        cmd = ["resize2fs", part_dev]
        log_lines.append(f"$ {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log_lines.append(r.stdout.strip())
        if r.stderr.strip():
            log_lines.append(r.stderr.strip())
        on_update({"progress": 1.0})
        return r.returncode == 0
    except FileNotFoundError:
        log_lines.append("WARN: resize2fs not found, skipping")
        on_update({"progress": 1.0})
        return True
    except Exception as exc:
        log_lines.append(f"ERROR resize: {exc}")
        on_update({"progress": 1.0})
        return False

