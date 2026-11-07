"""FastAPI backend for the dossier web UI. A thin HTTP wrapper over the same
oltaflock.pipeline used by run.py and streamlit_app.py -- no pipeline logic lives here.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from oltaflock.engine import load_rate_sheet  # noqa: E402
from oltaflock.pipeline import run_inquiry  # noqa: E402

DATA_DIR = ROOT / "data"
STATIC_DIR = Path(__file__).parent / "static"

logger = logging.getLogger("oltaflock.webapp")

app = FastAPI(title="Oltaflock Desk")


class RunRequest(BaseModel):
    inquiry_id: str
    text: str


@app.get("/api/rate-sheet")
def get_rate_sheet():
    rows = load_rate_sheet(DATA_DIR / "rate_sheet.csv")
    return [
        {
            "hotel_id": r.hotel_id,
            "name": r.name,
            "city": r.city,
            "room_type": r.room_type,
            "sleeps": r.sleeps,
            "price_per_night": r.price_per_night,
            "currency": r.currency,
            "wheelchair_accessible": r.wheelchair_accessible,
            "quiet_zone": r.quiet_zone,
            "adults_only": r.adults_only,
        }
        for r in rows
    ]


@app.get("/api/sample-inquiries")
def get_sample_inquiries():
    return json.loads((DATA_DIR / "inquiries.json").read_text(encoding="utf-8"))


@app.post("/api/run-inquiry")
def post_run_inquiry(req: RunRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Write or pick a client inquiry first.")
    rate_sheet = load_rate_sheet(DATA_DIR / "rate_sheet.csv")
    try:
        result = run_inquiry(req.inquiry_id, req.text, rate_sheet)
    except Exception as exc:
        # The exception's type/message is an internal implementation detail (a missing
        # package, a failed Azure call, a malformed LLM response) -- it is logged in full
        # server-side for debugging, but the client only ever sees a generic, safe
        # message plus a reference id it can quote back when asking for help.
        error_id = uuid.uuid4().hex[:8]
        logger.exception("run_inquiry failed [error_id=%s] inquiry_id=%s", error_id, req.inquiry_id)
        if isinstance(exc, (ModuleNotFoundError, ImportError)):
            detail = (
                f"The AI service isn't configured on this server yet "
                f"(reference {error_id}). Contact the administrator."
            )
        else:
            detail = (
                f"Couldn't generate a proposal right now (reference {error_id}). "
                f"Please try again in a moment."
            )
        raise HTTPException(status_code=502, detail=detail) from exc
    return json.loads(result.model_dump_json())


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
