# 🌡️ HeatGraph — FortyGuard Hackathon 2026

**NYC cross-departmental heat intelligence powered by the FortyGuard Large Temperature Model (LTM)**

HeatGraph ingests real 100-metre-grid FortyGuard temperature tiles, runs a Forney Factor Graph over every NYC entity (schools, hospitals, shelters, construction sites, subway stations…), surfaces high-risk receptors, computes cross-silo compound risks across city departments, and lets planners simulate new capital projects before approval — all in one dashboard.

---

## 🚀 How to open the dashboard in Chrome (for demo recording)

### Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Node.js | 18+ |
| Google Chrome | Latest |

### Step 1 — Install backend dependencies

```bash
cd "C:\Users\hp\FortyGuard Temperature AI\backend"
pip install -r requirements.txt
```

### Step 2 — Set environment variables

Create a `.env` file in the `backend/` folder (or export in your shell):

```
FORTYGUARD_API_KEY=<your-key>
GEMINI_API_KEY=<your-key>
MOCK_MODE=false
```

### Step 3 — Start the FastAPI backend

```bash
cd "C:\Users\hp\FortyGuard Temperature AI\backend"
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API is now live at `http://localhost:8000`.

### Step 4 — Start the React frontend (dev mode)

Open a **second** terminal:

```bash
cd "C:\Users\hp\FortyGuard Temperature AI\frontend"
npm install
npm run dev
```

Vite starts at `http://localhost:5173`. All `/api/*` requests are proxied to port 8000.

### Step 5 — Open in Chrome

1. Launch **Google Chrome**
2. Navigate to **`http://localhost:5173`**
3. The HeatGraph dashboard loads immediately

> **For demo recording:** Press `F11` for fullscreen, or use Chrome's built-in screen recorder (`⋮ → More tools → Cast, save, and share → Screen capture`) to record your demo video.

---

## 🎬 Demo walkthrough

| Step | Action |
|------|--------|
| 1 | Select your role (Emergency Mgmt, Planner, Public Health…) |
| 2 | Click **"Fetch Heatmap"** → FortyGuard LTM tiles render in colour |
| 3 | Open the **Risks** tab → risk scores for every NYC entity |
| 4 | Click **"View Details"** on any entity → FortyGuard satellite + street view imagery loads |
| 5 | Open the **Cross-silo** tab → compound risks between city departments |
| 6 | Switch to **Planner** role → click **"Simulate New Project"** → drop a pin → fill in the form → see before/after impact |
| 7 | Click **"What can I do?"** on any entity → Gemini generates live NYC programme recommendations |

---

## 🏗️ Architecture

```
FortyGuard API  ──►  FastAPI backend (port 8000)
                         │
                    ┌────┴──────────────────────────────┐
                    │  /api/heatmap   (LTM tiles)       │
                    │  /api/entities  (NYC open data)   │
                    │  /api/analysis  (factor graph)    │
                    │  /api/satellite (FortyGuard v1)   │
                    │  /api/streetview                  │
                    │  /api/solution  (Gemini)          │
                    │  /api/project-impact              │
                    └────┬──────────────────────────────┘
                         │  proxied via Vite (/api/*)
                    React + Leaflet frontend (port 5173)
```

**Key models:**
- **Forney Factor Graph** — each NYC entity type has a factor function; edge variables are FortyGuard 2-metre temperature values
- **Thermal decay** — ΔT at receptor = ΔT_site × exp(−d/200m) (Arnfield 2003 urban street canyon)
- **Compound risk** — cross-department pairs within 500 m, weighted by emitter ΔT × receptor risk score
- **Option B cache-read** — entities whose env-params have been fetched via "View Details" use real FortyGuard WBGT/AQI/PM2.5/solar in subsequent analysis runs (green ● indicator)

---

## 📦 Tech stack

| Layer | Stack |
|-------|-------|
| Frontend | React 18 + TypeScript + Vite + Leaflet |
| Backend | Python 3.11 + FastAPI + SQLAlchemy (SQLite cache) |
| AI / LLM | Google Gemini 1.5 Flash |
| Geospatial | FortyGuard LTM API · FortyGuard /v1/satellite · FortyGuard /v1/streetview |
| Thermal model | Forney Factor Graph + Arnfield 2003 decay |

---

## 🔑 Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FORTYGUARD_API_KEY` | ✅ | FortyGuard platform API key |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |
| `MOCK_MODE` | optional | Set `true` to use cached/mock data (no API calls) |
| `DATABASE_URL` | optional | SQLite path (default: `./heatgraph.db`) |

---

## 🏆 FortyGuard Hackathon 2026

Built for the **FortyGuard Temperature AI Hackathon** — demonstrating how the FortyGuard LTM, satellite imagery, and street-view segmentation can power cross-departmental climate intelligence for city planners and emergency managers.

**Track 4: Urban Resilience** — city-scale risk scoring, compound risk discovery, and project impact preview.
