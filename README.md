# ImpactGraph

### *The city runs on temperature. No one is watching.*

**FortyGuard Hackathon'26 · Track 4: Government & Environment**

---

Every day in New York City, hundreds of capital projects are approved. Each one goes through an environmental review — individually, in isolation, inside a single department. A road widening project in the Bronx gets reviewed by DOT. A school renovation gets reviewed by the School Construction Authority. A new NYCHA building gets reviewed by the Housing Authority.

None of them are required to ask: *what does this project do to the temperature around everything else nearby?*

This is the problem ImpactGraph was built to solve.

---

## What ImpactGraph Does

ImpactGraph models New York City as a **Forney-style factor graph** where **temperature is the shared edge variable** connecting city entities that have never been placed on the same map before.

A construction permit removing tree canopy in the South Bronx (Department of Buildings) raises the ambient temperature at a nearby shelter without air conditioning (Department of Homeless Services). That shelter's 150 residents — already some of the city's most vulnerable people — are now exposed to higher heat. Neither the Buildings Department nor Homeless Services has any visibility into this connection. No existing tool shows it. ImpactGraph does.

We call this a **cross-silo compound risk**: two entities from different city departments, coupled through the temperature field they share but neither owns.

**ImpactGraph discovers these compound risks automatically, explains them in plain language, and tells each user exactly what they can do about it.**

---

## The Problem Is Not That Cities Don't Care. It's That They Can't See.

The United States has some of the most data-rich city governments in the world. New York City publishes thousands of datasets. The Department of Buildings publishes every active construction permit. The MTA publishes every subway station. NYC DOHMH publishes a Heat Vulnerability Index for every neighborhood.

But these datasets live in separate silos, built for separate audiences, reviewed by separate departments. No one has ever put them together on a thermal map and asked: *when a DOT project raises the temperature in a corridor, who else in that corridor is at risk?*

The answer, it turns out, is almost always: several people, from several departments, who have no idea what is happening to their shared environment.

**This is what ImpactGraph makes visible.**

---

## The Science Behind It

### FortyGuard Large Temperature Models (LTMs)

Conventional weather stations measure temperature at airports — kilometers from where people actually live and work. Satellite surface temperature measures rooftop heat, not the air 2 meters above the ground where humans breathe.

FortyGuard's LTMs produce **2-meter ambient air temperature** at **100-meter spatial resolution**, derived from fusing satellite signals, GIS layers, meteorology, and in-situ observations. Within a single New York City block, the temperature can vary by 8–10°F. ImpactGraph uses this data as the ground truth for every risk computation in the system.

### The Forney-Style Factor Graph

ImpactGraph's core computation is a [Forney-style factor graph](https://en.wikipedia.org/wiki/Factor_graph) — the same mathematical structure used in Active Inference and variational Bayesian inference.

In this graph:
- **Edges = temperature variables** measured by FortyGuard at each grid cell: ambient temperature, Heat Index, Wet-Bulb Globe Temperature (WBGT), AQI, PM2.5, NO₂, O₃, solar irradiance, humidity
- **Nodes = factor functions** — one per entity type, encoding a dose-response relationship between temperature and outcome

Two types of factor nodes:
- **Emitter factors**: entities that *perturb* the temperature field. A demolition site removing 35% of block canopy adds an estimated +2.3°F at 100m decay. A new building increases impervious surface thermal mass. Each emitter's perturbation is grounded in solar irradiance from FortyGuard's env_params endpoint and the Arnfield (2003) urban street canyon thermal decay model: ΔT(r) = ΔT₀ × exp(−r/200m)
- **Receptor factors**: entities *affected by* the temperature field. Each receptor has a specific dose-response formula from peer-reviewed literature:
  - **Schools**: 1% learning loss per °F above 72°F threshold; AC offsets 78% of effect (Park, Goodman, Hurwitz & Smith, *AEJ: Economic Policy*, 2020)
  - **Hospitals**: 2.7–3.1% ER surge per °C above 29°C threshold; compounded by AQI (Lin et al., NYC SPARCS data; AHA 2024 cardiovascular heat statement)
  - **Shelters**: indoor temperature = ambient + 4°C (masonry thermal mass, high occupancy, no AC); mortality risk curve from Maricopa County 2023 heat mortality data
  - **Subway stations**: platform WBGT = ambient WBGT + 8°C delta; OSHA NPRM work/rest cycles (89 FR 70698, 2024)
  - **NYCHA housing**: apparent indoor temperature incorporating real humidity from FortyGuard env_params; 58% of NYC heat deaths occur in non-AC homes (NYC DOHMH)

**A compound risk is detected when an emitter node and a receptor node share a temperature edge** — i.e., they fall within 500 meters of each other, their temperature fields overlap, and they are managed by different city departments.

### Environmental Justice Foundation

Research covering 108 US urban areas found that 94% of formerly redlined neighborhoods show elevated temperatures — on average 2.6°C (4.7°F) warmer, and up to 7°C (12.6°F) warmer in the most extreme cases (Hoffman, Shandas & Pendleton, *Climate*, 2020). ImpactGraph overlays HOLC redlining boundaries on top of the factor graph, making the historical causal chain explicit: 1930s federal housing policy → reduced canopy investment → elevated current temperatures → higher risk scores for existing entities in those zones.

---

## Data Sources

### FortyGuard API (primary thermal intelligence)

| Endpoint | What we use it for |
|---|---|
| `POST /v1/heatmap` | 15,976 tiles at 100m resolution over NYC; also `analytic_type=persistence` for thermal persistence analysis |
| `POST /v1/env_params` | Real WBGT, AQI, PM2.5, NO₂, O₃, solar GHI/DNI/DHI, humidity per entity location |
| `POST /v1/satellite` | Current land-cover segmentation (buildings, roads, trees, grass) per entity — replaces static 2015 tree census data |
| `POST /v1/heat_intelligence` | FortyGuard's own 5-category contextual PDF report for any location |
| `POST /v1/streetview` | Ground-level street scene segmentation |
| `GET /v1/status/{id}` | Async task polling for all endpoints |

### Public Datasets (entity layer)

| Dataset | Entities | Source |
|---|---|---|
| NYC DOB Active Permits | 469 construction permits (emitters) | NYC Open Data `rbx6-tga4` |
| NYC Capital Projects Database (CPDB) | 2,776 capital projects across 8+ agencies (emitters) | NYC DCP, EPSG:2263 reprojected |
| NYC School Locations | 695 schools with AC status heuristic (receptors) | NYC DOE |
| NYCHA Developments | 216 public housing developments, 177K apartments (receptors) | `phvi-damg`, MULTIPOLYGON centroid |
| MTA Subway Stations | 445 stations with underground/elevated flag (both) | data.ny.gov `5f5g-n3cz` |
| NYC Facilities Database | 3,202 hospitals/nursing homes + 264 shelters (receptors) | NYC Open Data `ji82-xba5` |
| Heat Vulnerability Index | 164 ZIP-code zones, ranks 1–5 (receptors) | NYC DOHMH `4mhf-duep` |
| NYC Cooling Centers | 60 facilities (sinks) | NYC OEM |
| HOLC Redlining Maps | Historical 1930s boundaries (equity overlay) | University of Richmond |

**Total: 9,676 entities across 10 types and 29 city departments**

---

## Key Features

### 1. Role-Based Intelligence
Users select their role on first visit: City Planner, Health Officer, School District, Infrastructure, or Community Member. The factor graph computation, entity visibility, network graph filter, and Gemini solution language all adapt to that role. A health officer sees shelters and hospitals surfaced first; a school administrator sees schools. Same data, different lens.

### 2. Compound Risk Detection
ImpactGraph runs pairwise cross-departmental matching across all entity combinations within 500m. The result: ranked compound risks showing which emitter-receptor pairs from different departments are thermally coupled without either department's knowledge. Currently detecting 200+ compound risks across NYC.

### 3. Project Impact Preview *(Track 4 centerpiece)*
A city planner drops a pin on the map, describes a proposed project (type, size, canopy removal), and immediately sees:
- Estimated ΔT at the site (FortyGuard baseline + emitter factor)
- Which existing entities' risk scores increase
- Which risk tier changes occur (LOW → MODERATE, MODERATE → HIGH)
- Which new compound risks the project creates with existing emitters
- Which departments are affected without knowing it
- Gemini-generated "Before You Approve" briefing with mitigation options

This is the architectural question the city currently cannot answer: *what does this project do to everyone around it?*

### 4. FortyGuard Environmental Parameters
Every entity detail panel shows real-time FortyGuard data: ambient temperature, Heat Index, WBGT, humidity, AQI, PM2.5, solar irradiance, and NO₂. When real FortyGuard env_params have been fetched for an entity's coordinates, subsequent factor graph computations use those real values (green ● indicator). The system becomes progressively more accurate as users explore.

### 5. Satellite Land-Cover Analysis
Clicking "Satellite" on any entity shows FortyGuard's current land-cover segmentation: the actual percentage of buildings, roads, trees, and grass at that location — and a plain-language explanation of why it is hot. "Extreme impervious surface (76%). Almost no cooling vegetation. Urban heat island at maximum intensity."

### 6. Forney Graph Network View
Switch from List to Network view to see the compound risk subgraph rendered as a force-directed graph using Cytoscape.js. Each node is a city entity, colored by department. Each edge is a compound thermal risk. Hover to highlight a node's connections (Obsidian-style). Filter by department or risk tier. Click any node to fly the map to that location.

### 7. Gemini-Powered Solution Layer
Every risk card has a "What can I do?" button. Gemini 2.0 Flash uses web search to pull current NYC programs, live DHS shelter census numbers, and active grant windows — then generates role-specific actions (what the user can do this week), existing programs (with real links), and an optimistic counterfactual (what improves if action is taken). Pre-generated silently for the top 3 compound risks on page load.

### 8. Thermal Persistence Analysis
Beyond temperature snapshots, ImpactGraph can query FortyGuard's `persistence` analytic type — identifying how many consecutive hours a location stayed above a critical threshold. This is the metric that reveals transformer recovery failure (equipment that never cools overnight) and nighttime heat exposure in non-AC homes.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│  React 18 + TypeScript + Leaflet + Cytoscape.js                 │
│                                                                  │
│  ProfileSelector → ImpactMap → InsightPanel → NetworkGraph       │
│  ProjectImpactForm → SolutionOverlay → GlossaryTooltips         │
└──────────────────────────┬──────────────────────────────────────┘
                           │ /api/* (Vite proxy)
┌──────────────────────────▼──────────────────────────────────────┐
│                      FASTAPI BACKEND                             │
│                                                                  │
│  /api/heatmap          FortyGuard LTM tile fetch + SQLite cache  │
│  /api/entities         9,676 NYC entities, bbox/role/type filter │
│  /api/analysis         Forney graph computation → risk scores    │
│  /api/project-impact   Hypothetical emitter propagation          │
│  /api/network          Cytoscape.js compound risk subgraph       │
│  /api/solution         Gemini 2.0 Flash solution generation      │
│  /api/live-context     Gemini web search for current NYC data    │
│  /api/satellite        FortyGuard land-cover segmentation        │
│  /api/heat-intelligence FortyGuard 5-category PDF report        │
│  /api/forecast         6-hour diurnal risk projection            │
│  /api/energy-insight   IEEE C57.91 transformer aging model       │
└──────┬──────────────────────────────────────────┬───────────────┘
       │                                          │
┌──────▼──────────┐                    ┌──────────▼──────────────┐
│  FortyGuard API │                    │   Google Gemini API      │
│  /v1/heatmap    │                    │   gemini-2.0-flash       │
│  /v1/env_params │                    │   + web search           │
│  /v1/satellite  │                    └─────────────────────────┘
│  /v1/streetview │
│  /v1/heat_intel │
│  /v1/status     │
└─────────────────┘
```

### The Factor Graph Computation (simplified)

```python
# For each entity in the city:
tile = fortyguard.heatmap_tile_at(entity.lat, entity.lon)
env  = fortyguard.env_params_at(entity.lat, entity.lon, tile.temp_c)
# env contains: WBGT, AQI, PM2.5, humidity, solar GHI, NO₂...

risk = FACTOR_REGISTRY[entity.type].compute(entity, env)
# e.g. SchoolFactor: learning_loss = (temp_f - 72) * 0.01 * (0.22 if has_ac else 1.0)
#      HospitalFactor: er_surge = (temp_c - 29) * 3.0 + (aqi - 50) / 10 * 2.0

# Compound risk: emitter ΔT propagates to nearby receptors
delta_t = construction_factor(emitter, env)  # solar_amplifier × canopy_removal × size
for receptor in receptors_within_500m(emitter):
    decay  = exp(-distance / 200)            # Arnfield 2003
    risk_after = receptor_factor(receptor, env_with(T + delta_t * decay))
    if risk_after.tier != risk_before.tier:  # tier change = critical alert
        emit_compound_risk(emitter, receptor)
```

---

## Installation

### Requirements

- Python 3.11+
- Node.js 18+

### Clone and set up

```bash
git clone https://github.com/kanishkchitransh/heatgraph-fortyguard.git
cd heatgraph-fortyguard
```

### Backend

```bash
cd backend
pip install -r requirements.txt

# Create .env file:
cat > .env << EOF
FORTYGUARD_API_KEY=your_fortyguard_key
GEMINI_API_KEY=your_gemini_key
MOCK_MODE=false
DATABASE_URL=sqlite:///./heatgraph.db
EOF

# Start the API server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The backend auto-seeds the database with 9,676 NYC entities on first startup.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

### First run

1. Select your role on the welcome screen
2. Click **Fetch Heatmap** — FortyGuard generates 15,976 temperature tiles for NYC
3. The compound risk analysis runs automatically
4. Explore the insight panel and network graph

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `FORTYGUARD_API_KEY` | ✅ | FortyGuard platform API key (hackathon trial) |
| `GEMINI_API_KEY` | ✅ | Google AI Studio key — starts with `AQ.` or `AIza` |
| `MOCK_MODE` | optional | `true` = use cached data, no API calls |
| `DATABASE_URL` | optional | SQLite path (default: `sqlite:///./heatgraph.db`) |

---

## Hackathon Context

**FortyGuard Hackathon'26 — Building the World's Temperature AI**
**Track 4: Government & Environment**
*"Point public resources at the people heat hits hardest — target relief by vulnerability, warn outdoor workers before thresholds are crossed, and time agriculture to the microclimate."*

ImpactGraph directly addresses all three directives:
- **Target relief by vulnerability** → compound risk detection finds which vulnerable facilities (shelters, schools, NYCHA) are thermally coupled to active emitters
- **Warn before thresholds are crossed** → Project Impact Preview warns planners before approval; 6-hour risk forecast shows upcoming threshold crossings
- **Time to the microclimate** → FortyGuard 2m ambient data, not airport weather stations

### Research Citations

- Park, Goodman, Hurwitz & Smith — "Heat and Learning" (*AEJ: Economic Policy*, 2020)
- Hoffman, Shandas & Pendleton — "Effects of Historical Housing Policies on Resident Exposure to Intra-Urban Heat" (*Climate 8(1):12*, 2020)
- Lin et al. — temperature-respiratory admission correlation, NYC SPARCS data
- AHA 2024 Scientific Statement — nonoptimal temperature and cardiovascular mortality
- Arnfield (2003) — urban street canyon thermal decay model
- IEEE C57.91 — transformer insulation thermal aging (Arrhenius equation)
- OSHA NPRM 89 FR 70698 (Aug 2024) — WBGT-based heat safety thresholds
- NYC DOHMH — Heat Vulnerability Index, heat mortality data
- Maricopa County Public Health — 2023 heat mortality data (645 deaths, 45% unsheltered)

---

*Built with FortyGuard's Large Temperature Model · Google Gemini 3.5 Flash · NYC Open Data*
