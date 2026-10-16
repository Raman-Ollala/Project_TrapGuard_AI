"""Small, readable wildlife alert rules for TrapGuard AI."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import hypot

CONFIG = {
    "TRAIL_WINDOW_SECONDS": 600,
    "TRAIL_MIN_COUNT": 3,
    "TRAIL_HIGH_COUNT": 5,
    "REPEAT_WINDOW_SECONDS": 60,
    "REPEAT_MIN_COUNT": 5,
    "STILL_DISTANCE": 0.025,
    "SPEED_JUMP_MULTIPLIER": 3.0,
    "DRAG_MAX_SPEED": 0.01,
    "DRAG_ASPECT_RATIO": 1.8,
    "COOLDOWN_SECONDS": 10,
}


def _parse_ts(value: str) -> datetime:
    """Parse our ISO timestamps in one place."""
    return datetime.fromisoformat(value)


def _distance(a, b) -> float:
    """Return normalized centroid distance between two sightings."""
    return hypot(float(a["cx"]) - float(b["cx"]), float(a["cy"]) - float(b["cy"]))


def trail_deviation_rule(camera: dict, animal_history: list[dict]) -> list[dict]:
    """Alert when animals repeatedly appear near roads/villages or breach protected areas."""
    if not animal_history:
        return []
    now = _parse_ts(animal_history[-1]["ts"])
    recent = [x for x in animal_history if now - _parse_ts(x["ts"]) <= timedelta(seconds=CONFIG["TRAIL_WINDOW_SECONDS"])]
    count = len(recent)
    risky_zone = camera["zone_tag"] in {"road", "village"} or int(camera["protected"]) == 1
    if risky_zone and count >= CONFIG["TRAIL_MIN_COUNT"]:
        return [{
            "type": "trail_deviation",
            "severity": "high" if count >= CONFIG["TRAIL_HIGH_COUNT"] else "warning",
            "message": "Trail deviation: animal near road/human settlement",
        }]
    return []


def movement_anomaly_rules(animal_history: list[dict], still_seconds: int) -> list[dict]:
    """Detect inactivity, repeat triggering, sudden speed jumps and dragging posture."""
    if len(animal_history) < 2:
        return []
    alerts = []
    latest = animal_history[-1]
    latest_ts = _parse_ts(latest["ts"])

    within_60 = [x for x in animal_history if latest_ts - _parse_ts(x["ts"]) <= timedelta(seconds=CONFIG["REPEAT_WINDOW_SECONDS"])]
    if len(within_60) >= CONFIG["REPEAT_MIN_COUNT"]:
        alerts.append({"type": "repeated_triggering", "severity": "warning", "message": "Repeated triggering — possible crime spot"})

    # Prolonged inactivity: compare oldest point inside STILL_SECONDS to the latest point.
    still_candidates = [x for x in animal_history if latest_ts - _parse_ts(x["ts"]) <= timedelta(seconds=still_seconds)]
    if len(still_candidates) >= 2:
        span = (latest_ts - _parse_ts(still_candidates[0]["ts"])).total_seconds()
        if span >= max(1, still_seconds - 1) and _distance(still_candidates[0], latest) < CONFIG["STILL_DISTANCE"]:
            alerts.append({"type": "prolonged_inactivity", "severity": "high", "message": "Prolonged inactivity — possible injury/distress"})

    # Compare movement speeds for sudden changes.
    if len(animal_history) >= 4:
        recent = animal_history[-4:]
        speeds = []
        for a, b in zip(recent, recent[1:]):
            dt = max((_parse_ts(b["ts"]) - _parse_ts(a["ts"])).total_seconds(), 0.001)
            speeds.append(_distance(a, b) / dt)
        baseline = (speeds[0] + speeds[1]) / 2
        if baseline > 0 and speeds[-1] > CONFIG["SPEED_JUMP_MULTIPLIER"] * baseline:
            alerts.append({"type": "sudden_movement", "severity": "warning", "message": "Sudden movement change — possible distress/chase"})

    # Dragging heuristic: very slow movement plus a wide, low-looking box.
    prev = animal_history[-2]
    dt = max((latest_ts - _parse_ts(prev["ts"])).total_seconds(), 0.001)
    speed = _distance(prev, latest) / dt
    aspect = float(latest["w"]) / max(float(latest["h"]), 0.001)
    if speed <= CONFIG["DRAG_MAX_SPEED"] and aspect >= CONFIG["DRAG_ASPECT_RATIO"]:
        alerts.append({"type": "dragging", "severity": "high", "message": "Animal dragging itself — possible injury or poaching nearby"})

    return alerts


def interaction_rules(camera: dict, detections: list[dict]) -> list[dict]:
    """Create human/vehicle interaction alerts using a single frame of detections."""
    groups = {d["group"] for d in detections}
    has_person = "human" in groups
    has_vehicle = "vehicle" in groups
    has_animal = "animal" in groups
    suspicious = any(d["group"] == "object" for d in detections)
    alerts = []

    if int(camera["protected"]) == 1 and (has_person or has_vehicle):
        alerts.append({"type": "human_vehicle", "severity": "high", "message": "Human/vehicle in protected zone"})
    if has_person and has_animal:
        alerts.append({"type": "human_animal", "severity": "critical", "message": "Human near animal — possible poaching/interference"})
    if has_person and suspicious:
        alerts.append({"type": "suspicious_object", "severity": "critical", "message": "Person with suspicious object — possible poaching risk"})

    return alerts
