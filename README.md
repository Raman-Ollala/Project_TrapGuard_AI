# TrapGuard AI

A simple, demo-ready wildlife camera-trap monitoring web app for Tadoba Tiger Reserve. It works for any supported COCO-SSD animal class and is designed for forest rangers and an admin.

## Architecture

- Frontend: plain HTML, CSS and vanilla JavaScript served by FastAPI from `/static`
- Browser AI: TensorFlow.js `4.20.0` + COCO-SSD `2.2.3`, `lite_mobilenet_v2`
- Backend: FastAPI + standard-library `sqlite3`
- Database file: `trapguard.db`, auto-created and seeded
- Snapshots: saved automatically in `/snapshots`

## Demo sign-in

This is deliberately demo-level authentication only. PINs are stored as plain text in SQLite to keep the student demo understandable. Replace this with password hashing, secure sessions/JWTs and real authorization before any production use.

- Ranger Asha — `1111`
- Ranger Ravi — `2222`
- Ranger Neel — `3333`
- Admin — `9999`

## Run

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

Camera access works on `localhost`; on a phone or remote host, use HTTPS. A quick option is ngrok:

```bash
ngrok http 8000
```

Then set `PUBLIC_BASE_URL` in `.env` to the HTTPS ngrok URL so Telegram verification buttons point back to your app.

## Two-minute demo

1. Sign in as **Ranger Asha / 1111**. Open Camera Detection and press **Start Camera**. COCO-SSD loads in the browser; supported animals get green boxes and people/vehicles/objects get red boxes.
2. If you do not have wildlife nearby, open **Simulate Demo Alerts** and press **Trail Deviation**, **Inactivity**, **Dragging**, etc. The Alerts page updates automatically.
3. Press **Protected Human**. A pending verification alert appears. Choose **It's me** to mark it safe, or **Not me** to create a crime alert.
4. Leave a protected-human alert unanswered. After `ESCALATION_SECONDS`, the background loop checks the patrol schedule. Cam-04 has a seeded current patrol, so it becomes `scheduled_ranger`; an unscheduled case becomes `escalated`.
5. Sign out and sign in as **Admin / 9999**. Open Admin Dashboard to show stats, hotspots, patrol schedule, camera nodes and escalated/crime alerts.
6. To demo mobile behavior, open the HTTPS URL on a phone. The navigation moves to the bottom and verification appears as a bottom sheet.

## Rule summary

- Trail deviation: repeated animal sightings near road/village or a protected-zone breach
- Movement anomaly: prolonged inactivity, repeated triggering, sudden speed change, dragging heuristic
- Interaction: protected-zone people/vehicles, person + animal, or person + suspicious object
- A per-camera/type 10-second cooldown prevents alert spam

## Folder structure

```text
trapguard_ai/
├── main.py
├── rules.py
├── db.py
├── requirements.txt
├── .env.example
├── README.md
├── snapshots/          # auto-created / generated snapshots
└── static/
    ├── index.html
    ├── style.css
    ├── app.js
    ├── manifest.json
    └── sw.js
```

## Important assumptions / future improvements

- The sign-in flow is intentionally not secure authentication.
- COCO-SSD does not recognize species such as tiger or leopard by name; this demo maps the supported animal classes to a generic `Animal`. A real deployment should use a custom wildlife model.
- Browser detections use pixel-space boxes; the anomaly rules are intentionally simple heuristics for a student demo, not scientific wildlife-behavior models.
- Camera coordinates are seeded demo coordinates in the Tadoba region and are not operational field locations.
- Telegram URL buttons need a public HTTPS `PUBLIC_BASE_URL` such as ngrok or a deployed Render URL.
- The PWA service worker caches only the app shell, not APIs, snapshots or AI model CDN files.
