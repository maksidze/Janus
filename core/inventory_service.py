"""
Janus — Inventory service: drives, ports, images.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import List

from core.models import DriveInfo, ImageInfo

log = logging.getLogger("janus.inventory")

# Directory for .img / .img.xz / .img.gz / .iso files
IMAGES_DIR = Path(os.environ.get("JANUS_IMAGES_DIR",
                                  str(Path(__file__).resolve().parent.parent / "images")))

IMAGE_EXTENSIONS = {".img", ".iso", ".img.xz", ".img.gz", ".img.bz2", ".img.zst"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _get_root_device() -> str:
    """Return the block device that holds /."""
    try:
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            text=True, timeout=5,
        ).strip()
        # /dev/sda1 → /dev/sda  or /dev/mmcblk0p1 → /dev/mmcblk0
        import re
        m = re.match(r"(/dev/(?:sd[a-z]|nvme\d+n\d+|mmcblk\d+))", out)
        return m.group(1) if m else out
    except Exception:
        return ""


def _by_path_map() -> dict[str, str]:
    """Map /dev/sdX → /dev/disk/by-path/... ."""
    result: dict[str, str] = {}
    by_path = Path("/dev/disk/by-path")
    if not by_path.is_dir():
        return result
    for link in by_path.iterdir():
        try:
            target = link.resolve()
            result[str(target)] = str(link)
        except Exception:
            pass
    return result


# ── Public API ───────────────────────────────────────────────────────────────

def list_drives(removable_only: bool = False) -> List[DriveInfo]:
    """List block devices using lsblk."""
    try:
        raw = subprocess.check_output(
            ["lsblk", "-J", "-b", "-o",
             "NAME,SIZE,TYPE,MOUNTPOINT,MOUNTPOINTS,VENDOR,MODEL,SERIAL,TRAN,RM,HOTPLUG"],
            text=True, timeout=10,
        )
    except FileNotFoundError:
        log.error("lsblk not found")
        return []
    except Exception as exc:
        log.error("lsblk failed: %s", exc)
        return []

    data = json.loads(raw)
    devices = data.get("blockdevices", [])

    root_dev = _get_root_device()
    bp_map = _by_path_map()

    result: List[DriveInfo] = []
    for d in devices:
        if d.get("type") != "disk":
            continue
        dev_path = f"/dev/{d['name']}"
        rm = bool(d.get("rm") or d.get("hotplug"))
        if removable_only and not rm:
            continue

        # Collect mountpoints from children
        mounts: list[str] = []
        children = d.get("children") or []
        for child in children:
            mp = child.get("mountpoint") or ""
            mps = child.get("mountpoints") or []
            if mp:
                mounts.append(mp)
            for m in mps:
                if m and m not in mounts:
                    mounts.append(m)
        # Also check parent mountpoint
        if d.get("mountpoint"):
            mounts.append(d["mountpoint"])

        is_sys = (dev_path == root_dev) or any(m == "/" for m in mounts)

        size_b = int(d.get("size") or 0)
        result.append(DriveInfo(
            device_path=dev_path,
            by_path=bp_map.get(dev_path, ""),
            model=(d.get("model") or "").strip(),
            serial=(d.get("serial") or "").strip(),
            vendor=(d.get("vendor") or "").strip(),
            size_bytes=size_b,
            size_human=_human_size(size_b),
            removable=rm,
            mounted=len(mounts) > 0,
            mountpoints=mounts,
            usb_speed=d.get("tran") or "",
            port_path=bp_map.get(dev_path, ""),
            is_system=is_sys,
        ))

    return result


def list_images() -> List[ImageInfo]:
    """Scan images directory for supported image files."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    result: List[ImageInfo] = []
    for f in sorted(IMAGES_DIR.iterdir()):
        if not f.is_file():
            continue
        # Check compound extensions like .img.xz
        suffixes = "".join(f.suffixes)
        if suffixes not in IMAGE_EXTENSIONS and f.suffix not in IMAGE_EXTENSIONS:
            continue
        stat = f.stat()
        result.append(ImageInfo(
            name=f.name,
            path=str(f),
            size_bytes=stat.st_size,
            size_human=_human_size(stat.st_size),
            mtime=stat.st_mtime,
            img_type=suffixes.lstrip(".") or f.suffix.lstrip("."),
        ))
    return result


def list_ports() -> list[dict]:
    """Return available USB port paths from /dev/disk/by-path (legacy, flat list)."""
    bp = Path("/dev/disk/by-path")
    if not bp.is_dir():
        return []
    ports = []
    for link in sorted(bp.iterdir()):
        try:
            target = str(link.resolve())
            ports.append({"port_path": str(link), "device": target})
        except Exception:
            pass
    return ports


def _usb_speed_from_path(port_path: str) -> str:
    """Detect USB version from by-path string topology."""
    p = port_path.lower()
    if "usb3" in p or "usbv3" in p:
        return "3.0"
    if "usb2" in p or "usbv2" in p:
        return "2.0"
    # Try to read speed from sysfs using the USB topology
    # e.g. pci-0000:00:14.0-usb-0:5:1.0 -> bus 0:5 -> /sys/bus/usb/devices/...
    import re
    # Extract topology like 0:5:1.0 from path
    m = re.search(r'usb[v23]*-(\d+:\d+(?::\d+\.?\d*)*)', port_path)
    if m:
        topo = m.group(1)
        parts = topo.split(":")
        if len(parts) >= 2:
            busnum = parts[0]
            devpath = parts[1]
            # /sys/bus/usb/devices/<busnum>-<devpath>
            sysfs_dev = Path(f"/sys/bus/usb/devices/{busnum}-{devpath}")
            speed_file = sysfs_dev / "speed"
            if speed_file.exists():
                try:
                    speed = speed_file.read_text().strip()
                    mbps = int(speed)
                    if mbps >= 5000:
                        return "3.2"
                    elif mbps >= 480:
                        return "2.0"
                    else:
                        return "1.1"
                except Exception:
                    pass
    return "unknown"


def _short_port_alias(port_path: str) -> str:
    """
    Generate a human-readable short alias from the by-path string.
    e.g. /dev/disk/by-path/pci-0000:00:14.0-usb-0:3:1.0-scsi-0:0:0:0
         → 'USB 0:3'
    """
    import re
    name = Path(port_path).name
    # Match USB topology pattern
    m = re.search(r'usb[v23]*-(\d+:\d+(?:\.\d+)?)', name)
    if m:
        return f"USB {m.group(1)}"
    # Fallback: last 20 chars
    return name[-20:] if len(name) > 20 else name


def list_physical_ports() -> list[dict]:
    """
    Return a deduplicated list of physical USB ports (disk-level only, no partition entries).

    Each entry:
      port_path   - stable /dev/disk/by-path/... identifier (disk, not partition)
      alias       - short human name, e.g. 'USB 0:3'
      usb_speed   - '2.0' / '3.0' / 'unknown'
      device_path - /dev/sdX currently plugged in (or "")
      device_model - model string (or "")
      device_size  - human size (or "")
      device_serial - serial (or "")
      occupied    - bool: a drive is currently plugged in this port
    """
    bp = Path("/dev/disk/by-path")
    if not bp.is_dir():
        return []

    # Build reverse map: by_path → DriveInfo
    drives = list_drives(removable_only=False)
    drive_by_bypath: dict[str, "DriveInfo"] = {}
    for d in drives:
        if d.by_path:
            drive_by_bypath[d.by_path] = d

    seen: set[str] = set()
    result: list[dict] = []

    import re
    for link in sorted(bp.iterdir()):
        name = link.name
        # Skip partition entries (end in -partN)
        if re.search(r'-part\d+$', name):
            continue
        # Also skip scsi-lun entries that are partition-like
        if re.search(r'lun-\d+-part\d+$', name):
            continue

        port_path = str(link)
        if port_path in seen:
            continue
        seen.add(port_path)

        try:
            dev_target = str(link.resolve())
        except Exception:
            dev_target = ""

        # Find the drive for this port
        drive = drive_by_bypath.get(port_path)
        # Also try to match by resolved device path
        if not drive and dev_target:
            for d in drives:
                if d.device_path == dev_target:
                    drive = d
                    break

        usb_speed = _usb_speed_from_path(port_path)

        result.append({
            "port_path": port_path,
            "alias": _short_port_alias(port_path),
            "usb_speed": usb_speed,
            "device_path": drive.device_path if drive else "",
            "device_model": drive.model if drive else "",
            "device_size": drive.size_human if drive else "",
            "device_serial": drive.serial if drive else "",
            "device_vendor": drive.vendor if drive else "",
            "removable": drive.removable if drive else False,
            "is_system": drive.is_system if drive else False,
            "occupied": drive is not None,
        })

    return result


def unmount_device(device_path: str) -> tuple[bool, str]:
    """Attempt to unmount all partitions of a device."""
    try:
        # Find partitions
        raw = subprocess.check_output(
            ["lsblk", "-J", "-n", "-o", "NAME,MOUNTPOINT", device_path],
            text=True, timeout=10,
        )
        data = json.loads(raw)
        for bd in data.get("blockdevices", []):
            for child in (bd.get("children") or [bd]):
                mp = child.get("mountpoint")
                if mp:
                    dev = f"/dev/{child['name']}"
                    subprocess.check_call(["umount", dev], timeout=15)
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


def eject_device(device_path: str) -> tuple[bool, str]:
    """Unmount and eject (power off) a USB device."""
    ok, msg = unmount_device(device_path)
    if not ok:
        return False, f"unmount failed: {msg}"
    try:
        subprocess.check_call(
            ["udisksctl", "power-off", "-b", device_path, "--no-user-interaction"],
            timeout=15,
        )
        return True, "ejected"
    except FileNotFoundError:
        # udisksctl not installed, try eject
        try:
            subprocess.check_call(["eject", device_path], timeout=15)
            return True, "ejected"
        except Exception as exc:
            return False, str(exc)
    except Exception as exc:
        return False, str(exc)

