"""
Janus — SD Card Mass Flasher.
"""
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router

# Check run as root
import os
if os.geteuid() != 0:
    print("WARNING: Janus is not running as root. USB access may be limited, and flashing may fail.")
else:
    print("Running as root: full USB access enabled.")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Janus — SD Card Mass Flasher",
    description="Operator-style web UI for mass-flashing SD cards via USB hubs",
    version="1.0.0",
)

WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

app.include_router(router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    # Ensure data and images directories exist
    (Path(__file__).parent / "data").mkdir(exist_ok=True)
    (Path(__file__).parent / "images").mkdir(exist_ok=True)
    # Initialize layout file if not exists
    from core.layout_service import get_layout
    get_layout()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
