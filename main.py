"""TrapGuard AI FastAPI backend.

Run after installing requirements:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import get_conn, init_db, rows_to_dicts
from rules import CONFIG, interaction_rules, movement_anomaly_rules, trail_deviation_rule
from telegram_utils import send_telegram_photo, send_telegram_text

load_dotenv()
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
SNAPSHOT_DIR = BASE_DIR / "snapshots"
ESCALATION_SECONDS = int(os.getenv("ESCALATION_SECONDS", "60"))
STILL_SECONDS = int(os.getenv("STILL_SECONDS", "15"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
RANGER_CHAT_ID = os.getenv("RANGER_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")


class SightingIn(BaseModel):
    camera_id: int
    detections: list[dict]
    snapshot_base64: str | None = None


class VerifyIn(BaseModel):
    action: str
    ranger_name: str


class LoginIn(BaseModel):
    name: str
    pin: str


def utcnow() -> str:
    """Return a timezone-naive UTC ISO timestamp for easy SQLite comparisons."""
    return datetime.utcnow().isoformat(timespec="seconds")


def save_snapshot(data_url: str | None) -> str | None:
    """Save a JPEG data URL into /snapshots and return its relative URL path."""
    if not data_url:
        return None
    try:
        encoded = data_url.split(",", 1)[1] if "," in data_url else data_url
        raw = base64.b64decode(encoded)
        name = f"snap-{uuid.uuid4().hex[:12]}.jpg"
        path = SNAPSHOT_DIR / name
        path.write_bytes(raw)
        return f"/snapshots/{name}"
    except Exception as exc:
        print(f"Snapshot save failed: {exc}")
        return None


def filesystem_path(url_path: str | None) -> str | None:
    """Convert /snapshots/file.jpg into a real local file path."""
    if not url_path:
        return None
    return str(BASE_DIR / url_path.lstrip("/"))


def camera_or_404(camera_id: int) -> dict:
    """Fetch one camera or raise a friendly 404."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id=?", (camera_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Camera not found")
    return dict(row)


def alert_recently_exists(camera_id: int, alert_type: str) -> bool:
    """Simple per-camera/type cooldown to avoid repeated alert spam."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ts FROM alerts WHERE camera_id=? AND type=? ORDER BY id DESC LIMIT 1",
            (camera_id, alert_type),
        ).fetchone()
    if not row:
        return False
    age = (datetime.utcnow() - datetime.fromisoformat(row["ts"])).total_seconds()
    return age < CONFIG["COOLDOWN_SECONDS"]


async def create_alert(camera_id: int, alert_type: str, severity: str, message: str, snapshot_path: str | None, force_status: str | None = None) -> dict | None:
    """Insert one alert, send Telegram when needed and return the created row."""
    if alert_recently_exists(camera_id, alert_type):
        return None
    status = force_status or ("pending" if alert_type in {"human_vehicle", "human_animal", "suspicious_object"} else "resolved")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO alerts(ts,camera_id,type,severity,message,snapshot_path,status) VALUES(?,?,?,?,?,?,?)",
            (utcnow(), camera_id, alert_type, severity, message, snapshot_path, status),
        )
        alert_id = cur.lastrowid
        row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    alert = dict(row)
    camera = camera_or_404(camera_id)

    if status == "pending":
        buttons = [
            {"text": "✅ It's me", "url": f"{PUBLIC_BASE_URL}/verify/{alert_id}?action=ranger"},
            {"text": "❌ Not me", "url": f"{PUBLIC_BASE_URL}/verify/{alert_id}?action=not_me"},
        ]
        caption = f"⚠ Someone has passed through {camera['name']}. Was this you?\n{message}"
        await send_telegram_photo(RANGER_CHAT_ID, filesystem_path(snapshot_path), caption, buttons)
    elif severity in {"high", "critical"}:
        await send_telegram_photo(ADMIN_CHAT_ID, filesystem_path(snapshot_path), f"TrapGuard AI: {message} at {camera['name']}")
    return alert


async def escalation_loop() -> None:
    """Every five seconds, escalate unanswered protected-zone human alerts."""
    while True:
        try:
            cutoff = datetime.utcnow().timestamp() - ESCALATION_SECONDS
            with get_conn() as conn:
                pending = rows_to_dicts(conn.execute("SELECT * FROM alerts WHERE status='pending' AND type IN ('human_vehicle','human_animal','suspicious_object')").fetchall())
            for alert in pending:
                if datetime.fromisoformat(alert["ts"]).timestamp() > cutoff:
                    continue
                with get_conn() as conn:
                    schedule = conn.execute(
                        "SELECT * FROM patrol_schedule WHERE camera_id=? AND start_time<=? AND end_time>=? LIMIT 1",
                        (alert["camera_id"], alert["ts"], alert["ts"]),
                    ).fetchone()
                    if schedule:
                        status = "scheduled_ranger"
                        msg = f"Likely ranger {schedule['ranger_name']} (scheduled), please confirm"
                    else:
                        status = "escalated"
                        msg = "UNVERIFIED PERSON in protected zone — possible crime"
                    conn.execute("UPDATE alerts SET status=? WHERE id=? AND status='pending'", (status, alert["id"]))
                await send_telegram_photo(ADMIN_CHAT_ID, filesystem_path(alert["snapshot_path"]), msg)
        except Exception as exc:
            print(f"Escalation loop error: {exc}")
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepare folders/database and start the escalation background loop."""
    STATIC_DIR.mkdir(exist_ok=True)
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    init_db()
    task = asyncio.create_task(escalation_loop())
    yield
    task.cancel()


app = FastAPI(title="TrapGuard AI", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/snapshots", StaticFiles(directory=SNAPSHOT_DIR), name="snapshots")


@app.get("/")
def home():
    """Serve the single-page frontend."""
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/login")
def login(payload: LoginIn):
    """Demo-only PIN lookup; this is intentionally not real authentication."""
    with get_conn() as conn:
        row = conn.execute("SELECT id,name,role FROM rangers WHERE name=? AND pin=?", (payload.name, payload.pin)).fetchone()
    if not row:
        raise HTTPException(401, "Incorrect officer or PIN")
    return dict(row)


@app.get("/api/rangers")
def rangers():
    """List names used by the demo sign-in dropdown."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id,name,role FROM rangers ORDER BY role,name").fetchall()
    return rows_to_dicts(rows)


@app.get("/api/cameras")
def cameras():
    """Return all configured field cameras."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM cameras ORDER BY id").fetchall()
    return rows_to_dicts(rows)


@app.post("/api/sightings")
async def sightings(payload: SightingIn):
    """Store detections, evaluate rules and create any resulting alerts."""
    camera = camera_or_404(payload.camera_id)
    snapshot_path = save_snapshot(payload.snapshot_base64)
    now = utcnow()
    normalized = []
    with get_conn() as conn:
        for d in payload.detections:
            bbox = d.get("bbox") or [0, 0, 0, 0]
            x, y, w, h = [float(v) for v in bbox]
            item = {
                "label": str(d.get("label", "unknown")),
                "group": str(d.get("group", "object")),
                "confidence": float(d.get("confidence", 0)),
                "cx": x + w / 2,
                "cy": y + h / 2,
                "w": w,
                "h": h,
                "ts": now,
            }
            normalized.append(item)
            conn.execute(
                'INSERT INTO sightings(camera_id,ts,label,"group",confidence,cx,cy,w,h) VALUES(?,?,?,?,?,?,?,?,?)',
                (payload.camera_id, now, item["label"], item["group"], item["confidence"], item["cx"], item["cy"], item["w"], item["h"]),
            )

    with get_conn() as conn:
        history = rows_to_dicts(conn.execute(
            'SELECT * FROM sightings WHERE camera_id=? AND "group"="animal" ORDER BY id DESC LIMIT 40',
            (payload.camera_id,),
        ).fetchall())[::-1]

    candidates = []
    candidates.extend(trail_deviation_rule(camera, history))
    candidates.extend(movement_anomaly_rules(history, STILL_SECONDS))
    candidates.extend(interaction_rules(camera, normalized))

    created = []
    for candidate in candidates:
        alert = await create_alert(payload.camera_id, candidate["type"], candidate["severity"], candidate["message"], snapshot_path)
        if alert:
            created.append(alert)
    return {"ok": True, "created_alerts": created}


@app.get("/api/alerts")
def alerts(status: str | None = None, limit: int = Query(100, ge=1, le=500)):
    """Return recent alerts, optionally filtered by status."""
    with get_conn() as conn:
        if status:
            rows = conn.execute("SELECT a.*, c.name camera_name FROM alerts a JOIN cameras c ON c.id=a.camera_id WHERE a.status=? ORDER BY a.id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = conn.execute("SELECT a.*, c.name camera_name FROM alerts a JOIN cameras c ON c.id=a.camera_id ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/alerts/{alert_id}")
def alert_one(alert_id: int):
    """Return one alert by ID."""
    with get_conn() as conn:
        row = conn.execute("SELECT a.*, c.name camera_name FROM alerts a JOIN cameras c ON c.id=a.camera_id WHERE a.id=?", (alert_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Alert not found")
    return dict(row)


@app.post("/api/alerts/{alert_id}/verify")
async def verify(alert_id: int, payload: VerifyIn):
    """Mark an alert as verified ranger or crime and notify admin on crime."""
    if payload.action not in {"ranger", "not_me"}:
        raise HTTPException(400, "action must be ranger or not_me")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Alert not found")
        status = "verified_ranger" if payload.action == "ranger" else "crime"
        conn.execute("UPDATE alerts SET status=?, resolved_by=?, resolved_at=? WHERE id=?", (status, payload.ranger_name, utcnow(), alert_id))
        camera = conn.execute("SELECT * FROM cameras WHERE id=?", (row["camera_id"],)).fetchone()
    if status == "crime":
        text = f"CRIME ALERT: {camera['name']} was marked 'Not me' by {payload.ranger_name}."
        await send_telegram_photo(ADMIN_CHAT_ID, filesystem_path(row["snapshot_path"]), text)
        await send_telegram_text(RANGER_CHAT_ID, text)
    return {"ok": True, "status": status}


@app.get("/verify/{alert_id}", response_class=HTMLResponse)
async def telegram_verify(alert_id: int, action: str):
    """Tiny HTML confirmation page used by Telegram URL buttons without webhooks."""
    name = "Telegram Ranger"
    try:
        await verify(alert_id, VerifyIn(action=action, ranger_name=name))
        label = "verified as ranger" if action == "ranger" else "reported as NOT ME / possible crime"
        return HTMLResponse(f"<h2>TrapGuard AI</h2><p>Alert #{alert_id} {label}.</p><p>You can close this page.</p>")
    except HTTPException as exc:
        return HTMLResponse(f"<h2>TrapGuard AI</h2><p>{exc.detail}</p>", status_code=exc.status_code)


@app.get("/api/stats")
def stats():
    """Return dashboard counts and per-camera hotspot totals."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        crimes = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='crime'").fetchone()[0]
        escalated = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='escalated'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
        hotspot = rows_to_dicts(conn.execute("SELECT c.id,c.name,COUNT(a.id) alert_count FROM cameras c LEFT JOIN alerts a ON a.camera_id=c.id GROUP BY c.id,c.name ORDER BY alert_count DESC").fetchall())
        by_type = rows_to_dicts(conn.execute("SELECT type,COUNT(*) count FROM alerts GROUP BY type ORDER BY count DESC").fetchall())
        by_severity = rows_to_dicts(conn.execute("SELECT severity,COUNT(*) count FROM alerts GROUP BY severity").fetchall())
        by_status = rows_to_dicts(conn.execute("SELECT status,COUNT(*) count FROM alerts GROUP BY status").fetchall())
    return {"total": total, "crimes": crimes, "escalated": escalated, "pending": pending, "hotspots": hotspot, "by_type": by_type, "by_severity": by_severity, "by_status": by_status}


@app.get("/api/schedule")
def schedule():
    """Return the seeded ranger patrol schedule."""
    with get_conn() as conn:
        rows = conn.execute("SELECT p.*, c.name camera_name FROM patrol_schedule p JOIN cameras c ON c.id=p.camera_id ORDER BY p.start_time").fetchall()
    return rows_to_dicts(rows)


@app.post("/api/simulate/{alert_type}")
async def simulate(alert_type: str):
    """Create useful demo alerts even when there is no live camera subject."""
    presets = {
        "trail": (2, "trail_deviation", "warning", "Trail deviation: animal near road/human settlement"),
        "inactivity": (4, "prolonged_inactivity", "high", "Prolonged inactivity — possible injury/distress"),
        "repeat": (4, "repeated_triggering", "warning", "Repeated triggering — possible crime spot"),
        "sudden": (4, "sudden_movement", "warning", "Sudden movement change — possible distress/chase"),
        "dragging": (4, "dragging", "high", "Animal dragging itself — possible injury or poaching nearby"),
        "human": (4, "human_vehicle", "high", "Human/vehicle in protected zone"),
        "poaching": (4, "human_animal", "critical", "Human near animal — possible poaching/interference"),
        "object": (4, "suspicious_object", "critical", "Person with suspicious object — possible poaching risk"),
    }
    if alert_type not in presets:
        raise HTTPException(404, "Unknown simulation type")
    camera_id, kind, severity, message = presets[alert_type]
    alert = await create_alert(camera_id, kind, severity, message, None)
    return {"ok": True, "alert": alert, "note": "If this returns null, the 10-second cooldown is active."}
