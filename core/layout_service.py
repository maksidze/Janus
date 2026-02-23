"""
Janus — Layout service: load/save grid configuration.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core.models import LayoutConfig, PortCell, UsbHint

log = logging.getLogger("janus.layout")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LAYOUT_FILE = DATA_DIR / "layout.json"


def _default_layout() -> LayoutConfig:
    """Generate a sensible default layout (2 rows × 4 cols = 8 cells)."""
    rows, cols = 2, 4
    cells = []
    for r in range(rows):
        for c in range(cols):
            label = chr(65 + r) + str(c + 1)  # A1 A2 … B4
            cells.append(PortCell(
                cell_id=label,
                label=label,
                port_id="",
                usb_hint=UsbHint.UNKNOWN,
                enabled=True,
            ))
    return LayoutConfig(rows=rows, cols=cols, cells=cells)


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_layout() -> LayoutConfig:
    ensure_data_dir()
    if not LAYOUT_FILE.exists():
        layout = _default_layout()
        save_layout(layout)
        return layout
    try:
        data = json.loads(LAYOUT_FILE.read_text(encoding="utf-8"))
        return LayoutConfig(**data)
    except Exception as exc:
        log.warning("Failed to parse layout.json, using default: %s", exc)
        return _default_layout()


def save_layout(layout: LayoutConfig):
    ensure_data_dir()
    LAYOUT_FILE.write_text(
        layout.model_dump_json(indent=2),
        encoding="utf-8",
    )
    log.info("Layout saved (%d cells)", len(layout.cells))


def export_layout_bytes() -> bytes:
    layout = get_layout()
    return layout.model_dump_json(indent=2).encode("utf-8")


def import_layout(raw: bytes) -> LayoutConfig:
    data = json.loads(raw)
    layout = LayoutConfig(**data)
    save_layout(layout)
    return layout

