/**
 * HeatMap — Leaflet map + dual-panel insight view.
 *
 * Layout:
 *   [Header: brand · role chips · view toggle · fetch button]
 *   [Legend + analysis summary]
 *   [Map 60%] | [Insight panel 40%] — List OR Network view
 *
 * Panel modes:
 *   List   → Live context banner + risk score cards + compound risk cards
 *   Network → Cytoscape force-directed graph of compound risk subgraph
 *
 * Solution overlay slides up from the bottom when the user clicks
 * "What can I do?" on any entity (in either panel mode).
 */
import { useState, useEffect, useCallback, useRef } from "react";
import {
  MapContainer, TileLayer, GeoJSON, Marker, Popup,
  Polyline, useMap, useMapEvents,
} from "react-leaflet";
import L from "leaflet";
import type { Feature, FeatureCollection } from "geojson";
import type { PathOptions } from "leaflet";
import "leaflet/dist/leaflet.css";

import type { HeatmapResponse, CityEntity, EntitiesResponse, RoleKey } from "../../types";
import { NYC, ROLES } from "../../types";
import type { AnalysisResponse, CompoundRisk, EntityScore } from "../../types/analysis";
import type { UserProfile } from "../Onboarding/ProfileSelector";
import { NetworkGraph, type NetworkNodeData } from "../Network/NetworkGraph";
import { Tip } from "../Glossary/GlossaryTooltip";
import { GuidedTour, shouldAutoStartTour } from "../Onboarding/GuidedTour";

// In production the frontend is served by FastAPI itself — use relative URLs.
// In dev, Vite's proxy (vite.config.ts) forwards /api/* to localhost:8000.
const API_BASE  = "";
const DEMO_DATE = "2024-08-23T14:00:00Z";

// ---------------------------------------------------------------------------
// Colours & icons
// ---------------------------------------------------------------------------
function tempCtoColor(tempC: number): string {
  const f = tempC * 9 / 5 + 32;
  if (f < 75)  return "rgba(30,100,220,0.55)";
  if (f < 85)  return "rgba(100,200,100,0.55)";
  if (f < 95)  return "rgba(255,200,0,0.55)";
  if (f < 105) return "rgba(255,120,0,0.55)";
  return "rgba(220,0,0,0.65)";
}
function tileStyle(feature?: Feature): PathOptions {
  const tempC = (feature?.properties?.average_temperature ?? 40) as number;
  return { fillColor: tempCtoColor(tempC), fillOpacity: 0.75, color: "none", weight: 0 };
}
function tilesTooltip(feature?: Feature): string {
  if (!feature?.properties) return "";
  const c = (feature.properties.average_temperature ?? 0) as number;
  return `${c.toFixed(1)}°C / ${(c * 9 / 5 + 32).toFixed(1)}°F`;
}
function scoreColor(score: number): string {
  if (score >= 80) return "#f87171";
  if (score >= 60) return "#fb923c";
  if (score >= 40) return "#facc15";
  return "#6ee7b7";
}

const ENTITY_ICONS: Record<string, string> = {
  school:               "🎓",
  hospital:             "🏥",
  shelter:              "🏠",
  tree_canopy:          "🌳",
  cooling_center:       "❄️",
  fire_station:         "🚒",
  capital_project:      "🏗️",
  construction_permit:  "🚧",
  nycha_development:    "🏢",
  subway_station:       "🚇",
  hvi_zone:             "🌡️",
  census_tract:         "📊",
};
const ROLE_COLORS: Record<string, string> = {
  emitter:  "#f59e0b",
  receptor: "#6ee7b7",
  both:     "#f472b6",
  sink:     "#34d399",
};

function makeEntityIcon(entityType: string, role: string, highlight = false): L.DivIcon {
  const emoji = ENTITY_ICONS[entityType] ?? "📍";
  const dot   = highlight ? "#fff" : (ROLE_COLORS[role] ?? "#aaa");
  const ring  = highlight ? "2px solid #f87171" : "1px solid #000a";
  return L.divIcon({
    className: "",
    html: `<div style="position:relative;display:inline-block">
      <div style="font-size:${highlight ? 24 : 20}px;line-height:1;filter:drop-shadow(0 1px 2px #000a)">${emoji}</div>
      <div style="position:absolute;bottom:-2px;right:-2px;width:7px;height:7px;border-radius:50%;background:${dot};border:${ring}"></div>
    </div>`,
    iconSize:    [24, 26],
    iconAnchor:  [12, 12],
    popupAnchor: [0, -14],
  });
}

// ---------------------------------------------------------------------------
// Viewport tracker
// ---------------------------------------------------------------------------
function BoundsTracker({ onChange }: { onChange: (bbox: string) => void }) {
  const map  = useMap();
  const fire = useCallback(() => {
    const b = map.getBounds();
    onChange(`${b.getWest().toFixed(5)},${b.getSouth().toFixed(5)},${b.getEast().toFixed(5)},${b.getNorth().toFixed(5)}`);
  }, [map, onChange]);
  useMapEvents({ moveend: fire, zoomend: fire });
  useEffect(() => { fire(); }, [fire]);
  return null;
}

// ---------------------------------------------------------------------------
// Map panner (used when NetworkGraph node is clicked)
// ---------------------------------------------------------------------------
function MapPanController({ target, onDone }: { target: [number, number] | null; onDone: () => void }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    map.flyTo(target, Math.max(map.getZoom(), 16), { duration: 0.8 });
    onDone();
  }, [target, map, onDone]);
  return null;
}

// ---------------------------------------------------------------------------
// Map click capture for project simulation mode
// ---------------------------------------------------------------------------
function MapClickController({ active, onMapClick }: { active: boolean; onMapClick: (ll: [number, number]) => void }) {
  useMapEvents({
    click(e) {
      if (active) onMapClick([e.latlng.lat, e.latlng.lng]);
    },
  });
  return null;
}

// ---------------------------------------------------------------------------
// Project Impact types
// ---------------------------------------------------------------------------
interface ProjectReceptorImpact {
  entity_id: string; entity_name: string; entity_type: string;
  department: string; distance_m: number;
  risk_before: number; risk_after: number; risk_change: number;
  tier_before: string; tier_after: string; tier_changed: boolean;
  effective_delta_t_f: number; lat: number; lon: number;
}
interface ProjectCompoundRisk {
  emitter_1: string; emitter_1_dept: string; emitter_2: string;
  receptor: string; receptor_dept: string; risk_after: number; insight: string;
}
interface ProjectImpactResult {
  project: {
    name: string; lat: number; lon: number; job_type: string; managing_dept: string;
    baseline_temp_f: number; estimated_delta_t_f: number; new_temp_f: number;
    dust_pm25_increase: number; env_source: string;
  };
  receptor_impacts: ProjectReceptorImpact[];
  new_compound_risks: ProjectCompoundRisk[];
  summary: string;
  stats: {
    receptors_in_range: number; receptors_affected: number; tier_changes: number;
    new_compound_risks: number; departments_unaware: number;
  };
}

// ---------------------------------------------------------------------------
// Solution card types
// ---------------------------------------------------------------------------
interface SolutionCard {
  entity_id:          string;
  entity_name:        string;
  what_you_can_do:    { action: string; effort: "low" | "medium" | "high"; detail: string }[];
  whats_happening:    { program: string; agency: string; detail: string; url: string | null }[];
  optimistic_outlook: string;
  live_context:       string;
  generated_by:       string;
}

interface LiveContext {
  context: {
    headline?:       string;
    temperature?:    string;
    cooling_centers?:string;
    shelter_census?: string;
    recent_events?:  string;
    active_programs?:string;
  };
  source:     string;
  cached_at?: string;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
interface HeatMapProps {
  initialProfile:  UserProfile;
  onChangeProfile: () => void;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export function HeatMap({ initialProfile, onChangeProfile }: HeatMapProps) {
  // Map to role key from profile
  const profileToRoleKey = (p: UserProfile): RoleKey => {
    if (p.primary_role === "infrastructure") return "infra";
    if (p.primary_role in ROLES) return p.primary_role as RoleKey;
    return "planner";
  };

  const [selectedRole, setSelectedRole] = useState<RoleKey>(profileToRoleKey(initialProfile));
  const [panelView,    setPanelView]    = useState<"list" | "network">("list");

  const [mapData,   setMapData]   = useState<FeatureCollection | null>(null);
  const [statsData, setStatsData] = useState<Record<string, unknown> | null>(null);
  const [entities,  setEntities]  = useState<CityEntity[]>([]);
  const [totalEntities, setTotal] = useState(0);
  const [viewBbox,  setViewBbox]  = useState<string>("");

  const [loading,   setLoading]   = useState(false);
  const [eLoading,  setELoading]  = useState(false);
  const [error,     setError]     = useState<string | null>(null);
  const [isCached,  setIsCached]  = useState(false);
  const [isMock,    setIsMock]    = useState(false);

  const [analysis,  setAnalysis]  = useState<AnalysisResponse | null>(null);
  const [aLoading,  setALoading]  = useState(false);
  const [aError,    setAError]    = useState<string | null>(null);

  const [activeCompound, setActiveCompound] = useState<CompoundRisk | null>(null);
  const [panelTab,       setPanelTab]       = useState<"scores" | "compound">("scores");

  // Solution overlay
  const [solution,     setSolution]     = useState<SolutionCard | null>(null);
  const [solutionOpen, setSolutionOpen] = useState(false);
  const [solutionLoading, setSolutionLoading] = useState(false);

  // Live context banner
  const [liveContext,  setLiveContext]  = useState<LiveContext | null>(null);

  // Network graph → map sync
  const [mapPanTarget, setMapPanTarget] = useState<[number, number] | null>(null);

  // Cross-panel entity highlight (list card click → network node highlight)
  const [highlightedEntityId, setHighlightedEntityId] = useState<string | null>(null);

  // Data source badge (FortyGuard tile info)
  const [dataSource, setDataSource] = useState<{
    tile_count: number; is_real_data: boolean; is_mock: boolean; cached_at: string | null;
  } | null>(null);

  // Guided tour — auto-starts on first visit
  const [tourActive, setTourActive] = useState(shouldAutoStartTour);

  // Energy insight (solar + transformer aging)
  const [energyInsight, setEnergyInsight] = useState<{
    temperature_f: number; solar_irradiance_wm2: number; humidity_pct: number;
    cooling_demand_increase_pct: number; transformer_aging_factor: number;
    transformer_risk: string; grid_insight: string;
  } | null>(null);

  const mapRef        = useRef<L.Map | null>(null);
  const solutionCache = useRef<Map<string, SolutionCard>>(new Map());
  const role          = ROLES[selectedRole];

  // ── Project Impact Simulation state (City Planner Track 4) ────────────────
  const [projectSimMode, setProjectSimMode] = useState(false);
  const [projectPin,     setProjectPin]     = useState<[number, number] | null>(null);
  const [projectSpec,    setProjectSpec]    = useState({
    name: "New Capital Project", job_type: "A1",
    project_size_m2: 2000, canopy_removal_pct: 20,
  });
  const [projectImpact,  setProjectImpact]  = useState<ProjectImpactResult | null>(null);
  const [projectLoading, setProjectLoading] = useState(false);

  const highlightedIds = activeCompound
    ? new Set([activeCompound.emitter_id, activeCompound.receptor_id])
    : new Set<string>();

  // ── Live context (fires once on mount) ──────────────────────────────────
  useEffect(() => {
    fetch(`${API_BASE}/api/live-context?city=nyc`)
      .then((r) => r.json())
      .then(setLiveContext)
      .catch(() => { /* silent */ });
  }, []);

  // ── Data source badge (fires once on mount) ───────────────────────────────
  useEffect(() => {
    fetch(`${API_BASE}/api/data-source`)
      .then((r) => r.json())
      .then(setDataSource)
      .catch(() => { /* silent */ });
  }, []);

  // ── Energy insight (fires once on mount) ─────────────────────────────────
  useEffect(() => {
    fetch(`${API_BASE}/api/energy-insight?city=nyc`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        // Only set if it has the expected shape (avoid setting error objects)
        if (d && typeof d.cooling_demand_increase_pct === "number") setEnergyInsight(d);
      })
      .catch(() => { /* silent */ });
  }, []);

  // ── Heatmap fetch ──────────────────────────────────────────────────────
  async function fetchHeatmap() {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${API_BASE}/api/heatmap`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ bbox: NYC.bbox, datetime: DEMO_DATE, granularity: 100 }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail || `HTTP ${resp.status}`);
      }
      const data: HeatmapResponse = await resp.json();
      setIsCached(!!data._cached);
      setIsMock(!!data._mock);
      if (!data.map_data) { setError("No map_data in response."); return; }
      const features = (data.map_data as FeatureCollection).features ?? [];
      if (features.length === 0) { setError("FortyGuard returned 0 tiles for NYC."); return; }
      setMapData(data.map_data as FeatureCollection);
      setStatsData(data.stats_data as Record<string, unknown> ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  // ── Entity fetch — viewport-driven ──────────────────────────────────────
  useEffect(() => {
    if (!viewBbox) return;
    setELoading(true);
    const params = new URLSearchParams({ city: "nyc", limit: "800", bbox: viewBbox });
    if (role.role_filter) params.set("role", role.role_filter);

    const fetches = role.entity_types && role.entity_types.length > 0
      ? role.entity_types.map((t) => {
          const p2 = new URLSearchParams(params);
          p2.set("type", t);
          p2.delete("role");
          return fetch(`${API_BASE}/api/entities?${p2}`).then((r) => r.json() as Promise<EntitiesResponse>);
        })
      : [fetch(`${API_BASE}/api/entities?${params}`).then((r) => r.json() as Promise<EntitiesResponse>)];

    Promise.all(fetches)
      .then((results) => {
        setEntities(results.flatMap((r) => r.entities ?? []));
        setTotal(Math.max(...results.map((r) => r.total ?? 0)));
      })
      .catch(() => setEntities([]))
      .finally(() => setELoading(false));
  }, [selectedRole, viewBbox, role]);

  // ── Analysis fetch ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!viewBbox) return;
    setALoading(true);
    setAError(null);
    fetch(`${API_BASE}/api/analysis`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ city: "nyc", bbox: viewBbox, compound_radius_m: 500, min_receptor_score: 20 }),
    })
      .then((r) => r.json() as Promise<AnalysisResponse>)
      .then((data) => { setAnalysis(data); setActiveCompound(null); })
      .catch((e) => setAError(String(e)))
      .finally(() => setALoading(false));
  }, [viewBbox, mapData]);

  // ── Pre-warm Gemini for top 3 compound risks (silent, background) ────────
  useEffect(() => {
    if (!analysis?.compound_risks?.length) return;
    const top3 = analysis.compound_risks.slice(0, 3);
    top3.forEach((c) => {
      if (solutionCache.current.has(c.receptor_id)) return;  // already cached
      const score = analysis.entity_scores.find((s) => s.entity_id === c.receptor_id);
      // Fire and forget — result lands in cache, user gets instant response
      fetch(`${API_BASE}/api/solution`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entity_id:        c.receptor_id,
          entity_name:      c.receptor_name,
          entity_type:      c.receptor_type,
          risk_score:       c.receptor_risk_score,
          temperature_f:    score?.temperature_f ?? 89,
          explanation:      c.insight,
          department:       c.receptor_department,
          compound_insight: c.insight,
          user_role:        role.api_role,
        }),
      })
        .then((r) => r.json())
        .then((card: SolutionCard) => {
          solutionCache.current.set(c.receptor_id, card);
        })
        .catch(() => { /* silent — pre-warm is best-effort */ });
    });
  }, [analysis]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ── Compound click ───────────────────────────────────────────────────────
  function onCompoundClick(c: CompoundRisk) {
    setActiveCompound(
      (prev) => (prev?.emitter_id === c.emitter_id && prev?.receptor_id === c.receptor_id ? null : c)
    );
    mapRef.current?.flyTo([c.mid_lat, c.mid_lon], 16, { duration: 0.8 });
  }

  // ── Solution fetch (checks pre-warm cache first) ─────────────────────────
  async function requestSolution(
    entityId: string, entityName: string, entityType: string,
    riskScore: number, temperatureF: number, explanation: string,
    department: string, compoundInsight?: string,
  ) {
    setSolutionOpen(true);
    setSolution(null);

    // Check pre-warm cache — top compound risks arrive here instantly
    const cached = solutionCache.current.get(entityId);
    if (cached) {
      setSolution(cached);
      setSolutionLoading(false);
      return;
    }

    setSolutionLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/api/solution`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          entity_id:       entityId,
          entity_name:     entityName,
          entity_type:     entityType,
          risk_score:      riskScore,
          temperature_f:   temperatureF,
          explanation:     explanation,
          department:      department,
          compound_insight: compoundInsight ?? null,
          user_role:       role.api_role,
        }),
      });
      const data: SolutionCard = await resp.json();
      solutionCache.current.set(entityId, data);  // cache for later
      setSolution(data);
    } catch (e) {
      console.error("Solution fetch failed:", e);
    } finally {
      setSolutionLoading(false);
    }
  }

  // Triggered from NetworkGraph "What can I do?"
  function onNetworkSolutionRequest(node: NetworkNodeData) {
    requestSolution(
      node.id, node.label, node.entity_type,
      node.risk_score, node.temperature_f, node.explanation,
      node.department,
    );
  }

  // Triggered from ScoreCard
  function onScoreSolution(s: EntityScore) {
    requestSolution(s.entity_id, s.entity_name, s.entity_type, s.risk_score, s.temperature_f, s.explanation, s.department);
  }

  // Triggered from CompoundCard
  function onCompoundSolution(c: CompoundRisk) {
    const score = analysis?.entity_scores.find((s) => s.entity_id === c.receptor_id);
    requestSolution(
      c.receptor_id, c.receptor_name, c.receptor_type,
      c.receptor_risk_score, score?.temperature_f ?? 89,
      c.insight, c.receptor_department, c.insight,
    );
  }

  // ── Project Impact simulation ─────────────────────────────────────────────
  async function runProjectSimulation() {
    if (!projectPin) return;
    setProjectLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/api/project-impact`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...projectSpec,
          latitude:      projectPin[0],
          longitude:     projectPin[1],
          managing_dept: selectedRole === "planner" ? "NYC DOB" : role.api_role,
        }),
      });
      const data: ProjectImpactResult = await resp.json();
      setProjectImpact(data);
    } catch (e) {
      console.error("Project impact failed:", e);
    } finally {
      setProjectLoading(false);
    }
  }

  const tileCount = mapData?.features?.length ?? 0;
  const tempStats = (statsData?.temperature_stats ?? statsData?.Temperature_stats) as Record<string, number> | undefined;
  const statsMin  = tempStats?.Minimum ?? null;
  const statsMax  = tempStats?.Maximum ?? null;
  const summary   = analysis?.summary;
  const topScores = analysis?.entity_scores.slice(0, 50) ?? [];
  const compounds = analysis?.compound_risks ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: "var(--bg)" }}>

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div style={{
        padding: "8px 16px", background: "var(--bg-card)",
        display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
        borderBottom: "1px solid var(--border)",
        boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
      }}>
        <span style={{ fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif', color: "var(--brand)", fontWeight: 800, fontSize: 17, letterSpacing: -0.4 }}>🌡️ ImpactGraph</span>
        <span style={{ color: "var(--fg-faint)", fontSize: 12 }}>|</span>
        <span style={{ color: "var(--fg-muted)", fontSize: 12, fontWeight: 500 }}>New York City</span>

        <button
          id="fetch-heatmap-btn"
          onClick={fetchHeatmap}
          disabled={loading}
          style={{
            padding: "5px 14px", borderRadius: 8, fontSize: 12, fontWeight: 700,
            background: loading
              ? "var(--bg-muted)"
              : (mapData && !isMock)
                ? "#15803d"
                : "var(--brand)",
            color: loading ? "var(--fg-subtle)" : "#fff", border: "none",
            cursor: loading ? "not-allowed" : "pointer",
            transition: "background 0.2s",
            boxShadow: loading ? "none" : "0 1px 4px rgba(47,111,214,0.25)",
          }}
        >
          {loading ? "⏳ Loading…" : (mapData && !isMock) ? "✅ Heatmap Loaded" : "🌡️ Fetch Heatmap"}
        </button>

        {/* FortyGuard data source badge */}
        {dataSource && (
          <span style={{
            fontSize: 10, padding: "2px 10px", borderRadius: 10,
            background: dataSource.is_real_data ? "#dcfce7" : (dataSource.is_mock ? "#fef2f2" : "#eff6ff"),
            color: dataSource.is_real_data ? "#166534" : (dataSource.is_mock ? "#b91c1c" : "var(--brand)"),
            border: `1px solid ${dataSource.is_real_data ? "#86efac" : (dataSource.is_mock ? "#fca5a5" : "#bfdbfe")}`,
            display: "inline-flex", alignItems: "center", gap: 4, fontWeight: 600,
          }}>
            {dataSource.is_real_data && (
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e", display: "inline-block" }} />
            )}
            {dataSource.is_real_data
              ? `FortyGuard · ${dataSource.tile_count.toLocaleString()} tiles`
              : dataSource.is_mock ? "⚠️ Mock data" : "⚠️ No tiles"}
          </span>
        )}

        {/* Role chips */}
        <div id="role-selector" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {(Object.entries(ROLES) as [RoleKey, typeof ROLES[RoleKey]][]).map(([key, r]) => (
            <button key={key} onClick={() => setSelectedRole(key)} style={{
              padding: "4px 12px", borderRadius: 20, fontSize: 11, fontWeight: 600,
              border: selectedRole === key ? `1.5px solid var(--brand)` : `1.5px solid var(--border)`,
              background: selectedRole === key ? "var(--brand)" : "var(--bg-card)",
              color: selectedRole === key ? "#fff" : "var(--fg-muted)",
              cursor: "pointer", transition: "all 0.15s",
            }}>
              {r.label}
            </button>
          ))}
        </div>

        {/* View toggle */}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <ViewToggle active={panelView === "list"} onClick={() => setPanelView("list")}>📋 List</ViewToggle>
          <ViewToggle id="network-tab" active={panelView === "network"} onClick={() => setPanelView("network")}>🕸 Network</ViewToggle>
        </div>

        {/* Change role */}
        <button
          onClick={onChangeProfile}
          style={{
            padding: "4px 10px", borderRadius: 6, fontSize: 10, color: "var(--fg-muted)",
            background: "transparent", border: "1px solid var(--border)", cursor: "pointer",
          }}
        >
          Change role
        </button>

        {/* Tour button */}
        <button
          onClick={() => setTourActive(true)}
          title="Take a guided tour of ImpactGraph"
          style={{
            padding: "4px 10px", borderRadius: 6, fontSize: 10, color: "var(--fg-subtle)",
            background: "transparent", border: "1px solid var(--border)", cursor: "pointer",
            display: "flex", alignItems: "center", gap: 3,
          }}
        >
          ❓ Tour
        </button>

        {/* Status */}
        <div style={{ display: "flex", gap: 10, fontSize: 11, flexWrap: "wrap", alignItems: "center" }}>
          {tileCount > 0 && (
            <span style={{ color: "#166534", fontWeight: 600 }}>
              {tileCount.toLocaleString()} tiles
              {isMock ? " (mock)" : isCached ? " (cached)" : " (live)"}
              {statsMin != null && ` · ${statsMin.toFixed(1)}–${statsMax!.toFixed(1)}°C`}
            </span>
          )}
          {entities.length > 0 && (
            <span style={{ color: "var(--brand)", fontWeight: 500 }}>
              {eLoading ? "…" : entities.length} shown
              {totalEntities > entities.length ? ` / ${totalEntities.toLocaleString()}` : ""}
            </span>
          )}
          {error && <span style={{ color: "#ef4444" }}>⚠ {error}</span>}
        </div>
      </div>

      {/* ── Legend + summary ─────────────────────────────────────────────── */}
      <div style={{
        padding: "4px 16px", background: "var(--bg-muted)",
        display: "flex", gap: 12, fontSize: 10, color: "var(--fg-muted)",
        borderBottom: "1px solid var(--border)", flexWrap: "wrap", alignItems: "center",
      }}>
        <span>● <span style={{ color: ROLE_COLORS.emitter }}><Tip term="emitter">emitter</Tip></span></span>
        <span>● <span style={{ color: ROLE_COLORS.receptor }}><Tip term="receptor">receptor</Tip></span></span>
        <span>● <span style={{ color: ROLE_COLORS.both }}>both</span></span>
        <span>● <span style={{ color: ROLE_COLORS.sink }}><Tip term="sink">sink</Tip></span></span>
        {summary && (
          <span style={{ marginLeft: "auto", display: "flex", gap: 10 }}>
            <span style={{ color: "var(--fg-subtle)" }}>{summary.scored_entities} scored</span>
            <span style={{ color: "#ef4444", fontWeight: 600 }}>{summary.high_risk_count} high-risk</span>
            <span style={{ color: "var(--compound)", fontWeight: 600 }}>
              <Tip term="compound risk">{summary.compound_risk_count} compound</Tip>
            </span>
            {summary.tile_count === 0 && <span style={{ color: "#f59e0b" }}>⚠ fetch heatmap first</span>}
          </span>
        )}
      </div>

      {/* ── Map + panel ───────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden", position: "relative" }}>

        {/* Map */}
        <div style={{ flex: "0 0 60%", position: "relative", cursor: projectSimMode ? "crosshair" : "default" }}>

          {/* City Planner: "Simulate New Project" floating button */}
          {selectedRole === "planner" && !projectPin && (
            <div style={{ position: "absolute", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 1000 }}>
              <button
                onClick={() => setProjectSimMode((v) => !v)}
                style={{
                  padding: "8px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700,
                  background: projectSimMode ? "#d97706" : "#2f6fd6",
                  color: "#fff", border: "none", cursor: "pointer",
                  boxShadow: "0 2px 12px rgba(47,111,214,0.35)",
                  animation: projectSimMode ? "pulse 1.5s infinite" : "none",
                }}
              >
                {projectSimMode ? "📍 Click map to place project…" : "🏗️ Simulate New Project"}
              </button>
            </div>
          )}

          <MapContainer
            center={NYC.center}
            zoom={NYC.zoom}
            style={{ height: "100%", width: "100%" }}
            ref={mapRef as React.RefObject<L.Map>}
          >
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              maxZoom={19}
            />
            <BoundsTracker onChange={setViewBbox} />
            <MapPanController target={mapPanTarget} onDone={() => setMapPanTarget(null)} />
            <MapClickController
              active={projectSimMode}
              onMapClick={(ll) => { setProjectPin(ll); setProjectSimMode(false); }}
            />

            {mapData && (
              <GeoJSON
                key={`heat-nyc-${tileCount}`}
                data={mapData}
                style={tileStyle}
                onEachFeature={(feature, layer) => { layer.bindTooltip(tilesTooltip(feature)); }}
              />
            )}

            {entities.map((e) => (
              <Marker
                key={e.id}
                position={[e.lat, e.lon]}
                icon={makeEntityIcon(e.entity_type, e.role, highlightedIds.has(e.id))}
              >
                <Popup>
                  <div style={{ minWidth: 200 }}>
                    <strong style={{ fontSize: 13 }}>{ENTITY_ICONS[e.entity_type] ?? "📍"} {e.name}</strong><br />
                    <span style={{ fontSize: 11, color: "#888" }}>
                      {e.entity_type.replace(/_/g, " ")} · {e.role}
                    </span>
                    {e.address && <><br /><span style={{ fontSize: 11 }}>{e.address}</span></>}
                    {(() => {
                      const s = analysis?.entity_scores.find((sc) => sc.entity_id === e.id);
                      return s ? (
                        <div style={{ marginTop: 6 }}>
                          <div style={{ padding: "4px 6px", background: "#f8fafc", borderRadius: 4, border: "1px solid #e2e8f0" }}>
                            <span style={{ color: scoreColor(s.risk_score), fontWeight: 700 }}>
                              Risk: {s.risk_score}/100
                            </span>
                            <div style={{ fontSize: 10, color: "#475569", marginTop: 2 }}>{s.explanation}</div>
                          </div>
                          <button
                            onClick={() => onScoreSolution(s)}
                            style={{
                              marginTop: 6, width: "100%", padding: "5px 0", borderRadius: 5,
                              fontSize: 11, fontWeight: 700, background: "#2f6fd6", color: "#fff",
                              border: "none", cursor: "pointer",
                            }}
                          >
                            🟢 What can I do?
                          </button>
                        </div>
                      ) : null;
                    })()}
                  </div>
                </Popup>
              </Marker>
            ))}

            {activeCompound && (
              <Polyline
                positions={[
                  [activeCompound.emitter_lat, activeCompound.emitter_lon],
                  [activeCompound.receptor_lat, activeCompound.receptor_lon],
                ]}
                pathOptions={{ color: "#f472b6", weight: 2.5, dashArray: "6 4", opacity: 0.9 }}
              />
            )}

            {/* Project pin — pulsing orange marker */}
            {projectPin && (
              <Marker
                position={projectPin}
                icon={L.divIcon({
                  html: `<div style="
                    width:20px;height:20px;border-radius:50%;
                    background:#f97316;border:3px solid #fff;
                    box-shadow:0 0 0 4px rgba(249,115,22,0.4),0 0 0 8px rgba(249,115,22,0.2);
                  "></div>`,
                  className: "",
                  iconSize: [20, 20],
                  iconAnchor: [10, 10],
                })}
              />
            )}

            {/* Project impact: lines from pin to affected tier-changed receptors */}
            {projectImpact && projectPin && projectImpact.receptor_impacts.filter((r) => r.tier_changed).map((r) => (
              <Polyline
                key={r.entity_id}
                positions={[projectPin, [r.lat, r.lon]]}
                pathOptions={{ color: "#ef4444", weight: 1.5, dashArray: "4 3", opacity: 0.7 }}
              />
            ))}
          </MapContainer>
        </div>

        {/* ── Right panel ───────────────────────────────────────────────── */}
        <div style={{
          flex: "0 0 40%", background: "var(--bg-card)", display: "flex",
          flexDirection: "column", borderLeft: "1px solid var(--border)",
          position: "relative", overflow: "hidden",
        }}>
          {panelView === "list" ? (
            <ListPanel
              liveContext={liveContext}
              energyInsight={energyInsight}
              aLoading={aLoading}
              aError={aError}
              summary={summary}
              topScores={topScores}
              compounds={compounds}
              panelTab={panelTab}
              setPanelTab={setPanelTab}
              activeCompound={activeCompound}
              onCompoundClick={onCompoundClick}
              onSolutionRequest={onScoreSolution}
              onCompoundSolution={onCompoundSolution}
              onHighlight={setHighlightedEntityId}
            />
          ) : (
            <NetworkGraph
              city="nyc"
              role={ROLES[selectedRole].api_role}
              highlightedEntityId={highlightedEntityId}
              onNodeClick={(id, lat, lon) => { setHighlightedEntityId(id); setMapPanTarget([lat, lon]); }}
              onSolutionRequest={onNetworkSolutionRequest}
            />
          )}

          {/* ── Solution overlay (right panel only) ────────────────────── */}
          {solutionOpen && (
            <SolutionOverlay
              solution={solution}
              loading={solutionLoading}
              onClose={() => setSolutionOpen(false)}
            />
          )}
        </div>
      </div>

      {/* ── Project Impact: spec form (appears after pin is dropped) ─────────── */}
      {projectPin && !projectImpact && (
        <div style={{
          position: "fixed", bottom: 0, left: 0, right: 0,
          background: "var(--bg-card)", borderTop: "2px solid var(--brand)",
          padding: 16, zIndex: 2000, boxShadow: "0 -4px 24px rgba(0,0,0,0.1)",
        }}>
          <div style={{ fontSize: 13, color: "var(--brand)", fontWeight: 700, marginBottom: 10 }}>
            🏗️ New Project at {projectPin[0].toFixed(4)}, {projectPin[1].toFixed(4)}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
            {[
              { label: "Project Name", field: "name", type: "text" },
              { label: "Size (m²)", field: "project_size_m2", type: "number" },
            ].map(({ label, field, type }) => (
              <div key={field}>
                <div style={{ fontSize: 10, color: "var(--fg-muted)", marginBottom: 3 }}>{label}</div>
                <input
                  type={type} value={(projectSpec as any)[field]}
                  onChange={(e) => setProjectSpec((p) => ({ ...p, [field]: type === "number" ? Number(e.target.value) : e.target.value }))}
                  style={{ width: "100%", padding: "5px 8px", background: "var(--bg-muted)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--fg)", fontSize: 11, boxSizing: "border-box" }}
                />
              </div>
            ))}
            <div>
              <div style={{ fontSize: 10, color: "var(--fg-muted)", marginBottom: 3 }}>Job Type</div>
              <select
                value={projectSpec.job_type}
                onChange={(e) => setProjectSpec((p) => ({ ...p, job_type: e.target.value }))}
                style={{ width: "100%", padding: "5px 8px", background: "var(--bg-muted)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--fg)", fontSize: 11 }}
              >
                <option value="NB">NB — New Building</option>
                <option value="DM">DM — Demolition</option>
                <option value="A1">A1 — Major Alteration</option>
              </select>
            </div>
            <div>
              <div style={{ fontSize: 10, color: "var(--fg-muted)", marginBottom: 3 }}>Canopy Removal %</div>
              <input
                type="number" min="0" max="100" value={projectSpec.canopy_removal_pct}
                onChange={(e) => setProjectSpec((p) => ({ ...p, canopy_removal_pct: Number(e.target.value) }))}
                style={{ width: "100%", padding: "5px 8px", background: "var(--bg-muted)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--fg)", fontSize: 11, boxSizing: "border-box" }}
              />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={runProjectSimulation}
              disabled={projectLoading}
              style={{
                flex: 1, padding: 10, borderRadius: 6,
                background: projectLoading ? "var(--bg-muted)" : "var(--brand)",
                color: projectLoading ? "var(--fg-subtle)" : "#fff", fontSize: 12, fontWeight: 700, border: "none", cursor: "pointer",
              }}
            >
              {projectLoading ? "⚡ Computing…" : "⚡ Run Impact Analysis"}
            </button>
            <button
              onClick={() => { setProjectPin(null); setProjectImpact(null); setProjectSimMode(false); }}
              style={{ padding: "10px 16px", borderRadius: 6, background: "transparent", color: "var(--fg-muted)", fontSize: 12, border: "1px solid var(--border)", cursor: "pointer" }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── Project Impact: results panel ─────────────────────────────────────── */}
      {projectImpact && (
        <div style={{
          position: "fixed", bottom: 0, left: 0, right: 0,
          background: "var(--bg-card)", borderTop: "2px solid #ef4444",
          padding: 16, zIndex: 2000, maxHeight: "60vh", overflowY: "auto",
          boxShadow: "0 -4px 24px rgba(0,0,0,0.1)",
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#ef4444" }}>⚠ PROJECT IMPACT PREVIEW</div>
            <button onClick={() => { setProjectImpact(null); setProjectPin(null); }}
              style={{ color: "var(--fg-subtle)", background: "none", border: "none", cursor: "pointer", fontSize: 16 }}>✕</button>
          </div>

          {/* Thermal footprint banner */}
          <div style={{ padding: "8px 12px", background: "#fef2f2", borderRadius: 6, marginBottom: 10, border: "1px solid #fca5a5" }}>
            <div style={{ fontSize: 11, color: "#b91c1c" }}>
              📍 <strong>{projectImpact.project.name}</strong> adds estimated{" "}
              <strong style={{ color: "#ef4444" }}>+{projectImpact.project.estimated_delta_t_f}°F</strong> to the area{" "}
              (FortyGuard baseline: {projectImpact.project.baseline_temp_f}°F → new: {projectImpact.project.new_temp_f}°F)
            </div>
          </div>

          {/* Stats bar */}
          <div style={{ display: "flex", gap: 20, marginBottom: 12 }}>
            {([
              ["Entities affected", projectImpact.stats.receptors_affected, "var(--fg)"],
              ["Tier changes",      projectImpact.stats.tier_changes,       "#d97706"],
              ["Compound risks",    projectImpact.stats.new_compound_risks, "#ef4444"],
              ["Depts. unaware",    projectImpact.stats.departments_unaware,"var(--compound)"],
            ] as [string, number, string][]).map(([label, val, color]) => (
              <div key={label} style={{ textAlign: "center" }}>
                <div style={{ fontSize: 22, fontWeight: 800, color }}>{val}</div>
                <div style={{ fontSize: 9, color: "var(--fg-subtle)" }}>{label}</div>
              </div>
            ))}
          </div>

          {/* Before/After receptor list */}
          <div style={{ fontSize: 11, color: "var(--brand)", fontWeight: 600, marginBottom: 6 }}>AFFECTED ENTITIES (before → after)</div>
          {projectImpact.receptor_impacts.map((r) => (
            <div key={r.entity_id} style={{
              padding: "8px 10px", marginBottom: 5, borderRadius: 6,
              background: r.tier_changed ? "#fef2f2" : "var(--bg-muted)",
              border: r.tier_changed ? "1px solid #fca5a5" : "1px solid var(--border)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: 11, color: "var(--fg)", fontWeight: 600 }}>{r.entity_name}</div>
                  <div style={{ fontSize: 9, color: "var(--fg-subtle)" }}>{r.department} · {r.distance_m.toFixed(0)}m away · +{r.effective_delta_t_f.toFixed(1)}°F</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>
                    <span style={{ color: "var(--fg-subtle)" }}>{r.risk_before.toFixed(0)}</span>
                    <span style={{ color: "var(--fg-faint)" }}> → </span>
                    <span style={{ color: r.risk_change > 10 ? "#ef4444" : "#d97706" }}>{r.risk_after.toFixed(0)}</span>
                  </div>
                  {r.tier_changed && (
                    <div style={{ fontSize: 9, color: "#ef4444" }}>{r.tier_before} → {r.tier_after}</div>
                  )}
                </div>
              </div>
            </div>
          ))}

          {/* New compound risks */}
          {projectImpact.new_compound_risks.length > 0 && (
            <>
              <div style={{ fontSize: 11, color: "var(--compound)", fontWeight: 600, marginTop: 10, marginBottom: 6 }}>
                🔗 NEW CROSS-SILO RISKS CREATED BY THIS PROJECT
              </div>
              {projectImpact.new_compound_risks.slice(0, 3).map((c, i) => (
                <div key={i} style={{ padding: "8px 10px", marginBottom: 5, borderRadius: 6, background: "#f5f3ff", border: "1px solid #ddd6fe" }}>
                  <div style={{ fontSize: 10, color: "var(--compound)" }}>{c.insight}</div>
                </div>
              ))}
            </>
          )}

          {/* Get Gemini briefing for this project */}
          <button
            onClick={() => {
              const maxRisk = Math.max(...projectImpact.receptor_impacts.map((r) => r.risk_after), 0);
              requestSolution(
                "hypothetical-new-project",
                projectImpact.project.name,
                "construction_permit",
                maxRisk,
                projectImpact.project.new_temp_f,
                projectImpact.summary,
                projectImpact.project.managing_dept,
                projectImpact.new_compound_risks[0]?.insight ?? "",
              );
            }}
            style={{
              width: "100%", marginTop: 12, padding: 10, borderRadius: 6,
              background: "var(--brand)", color: "#fff", fontSize: 12, fontWeight: 700,
              border: "none", cursor: "pointer",
            }}
          >
            🟢 What can be done before approval?
          </button>
          <div style={{ fontSize: 9, color: "var(--fg-subtle)", marginTop: 6, textAlign: "center" }}>
            ΔT from FortyGuard baseline ({projectImpact.project.env_source}) + construction emitter · decay exp(−d/200m)
          </div>
        </div>
      )}

      {/* ── Guided tour overlay ───────────────────────────────────────────── */}
      <GuidedTour active={tourActive} onClose={() => setTourActive(false)} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// View toggle button
// ---------------------------------------------------------------------------
function ViewToggle({ active, onClick, children, id }: { active: boolean; onClick: () => void; children: React.ReactNode; id?: string }) {
  return (
    <button
      id={id}
      onClick={onClick}
      style={{
        padding: "4px 10px", borderRadius: 6, fontSize: 11, fontWeight: 600,
        background: active ? "var(--brand)" : "var(--bg-card)",
        color:      active ? "#fff" : "var(--fg-muted)",
        border:     active ? "1px solid var(--brand)" : "1px solid var(--border)",
        cursor:     "pointer",
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// List panel (scores + compound)
// ---------------------------------------------------------------------------
interface EnergyInsight {
  temperature_f: number; solar_irradiance_wm2: number; humidity_pct: number;
  cooling_demand_increase_pct: number; transformer_aging_factor: number;
  transformer_risk: string; grid_insight: string;
}

interface ListPanelProps {
  liveContext:         LiveContext | null;
  energyInsight:       EnergyInsight | null;
  aLoading:            boolean;
  aError:              string | null;
  summary:             AnalysisResponse["summary"] | undefined;
  topScores:           EntityScore[];
  compounds:           CompoundRisk[];
  panelTab:            "scores" | "compound";
  setPanelTab:         (t: "scores" | "compound") => void;
  activeCompound:      CompoundRisk | null;
  onCompoundClick:     (c: CompoundRisk) => void;
  onSolutionRequest:   (s: EntityScore) => void;
  onCompoundSolution:  (c: CompoundRisk) => void;
  onHighlight:         (entityId: string) => void;
}

function ListPanel({ liveContext, energyInsight, aLoading, aError, summary, topScores, compounds, panelTab, setPanelTab, activeCompound, onCompoundClick, onSolutionRequest, onCompoundSolution, onHighlight }: ListPanelProps) {
  return (
    <>
      {/* Live context banner */}
      {liveContext?.context?.headline && (
        <div style={{
          padding: "7px 12px", fontSize: 11, lineHeight: 1.45,
          background: "#eff6ff", borderBottom: "1px solid #bfdbfe",
          color: "var(--brand)", flexShrink: 0,
        }}>
          <span style={{ fontWeight: 700, color: "var(--brand)", marginRight: 6 }}>📡 LIVE</span>
          {liveContext.context.headline}
          {liveContext.context.shelter_census && (
            <span style={{ color: "var(--fg-subtle)", fontSize: 10, marginLeft: 8 }}>
              · {liveContext.context.shelter_census}
            </span>
          )}
        </div>
      )}

      {/* Energy insight banner — FortyGuard solar + transformer use case */}
      {energyInsight && (
        <div style={{
          padding: "5px 12px", fontSize: 10, lineHeight: 1.45,
          background: energyInsight.transformer_aging_factor > 2.0 ? "#fefce8" : "var(--bg-muted)",
          borderBottom: "1px solid var(--border)",
          color: "var(--fg-muted)", flexShrink: 0,
          display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
        }}>
          <span style={{ fontWeight: 700, color: "#ca8a04" }}>⚡ GRID</span>
          <span>{energyInsight.cooling_demand_increase_pct.toFixed(0)}% above baseline demand</span>
          <span style={{ color: "var(--fg-faint)" }}>·</span>
          <span style={{
            color: energyInsight.transformer_aging_factor > 2.0 ? "#ef4444" :
                   energyInsight.transformer_aging_factor > 1.5 ? "#f59e0b" : "#16a34a",
            fontWeight: 600,
          }}>
            Transformer {energyInsight.transformer_aging_factor.toFixed(1)}× aging
          </span>
          <span style={{ color: "var(--fg-faint)" }}>·</span>
          <span>{energyInsight.solar_irradiance_wm2.toFixed(0)} W/m² solar</span>
          <span style={{ color: "var(--fg-subtle)", fontSize: 9, width: "100%", marginTop: 1, fontStyle: "italic" }}>
            FortyGuard solar irradiance + IEEE C57.91 transformer model
          </span>
        </div>
      )}

      {/* Tab bar */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        {([
          ["scores",   `⚠ Risks${summary ? ` (${summary.high_risk_count} high)` : ""}`],
          ["compound", `🔗 ${" "}${summary ? summary.compound_risk_count : 0} Cross-silo`],
        ] as [string, string][]).map(([key, label]) => (
          <button key={key} id={key === "compound" ? "compound-tab" : undefined}
            onClick={() => setPanelTab(key as "scores" | "compound")} style={{
            flex: 1, padding: "7px 4px", fontSize: 11, fontWeight: 600,
            background: "transparent",
            color:      panelTab === key ? "var(--brand)" : "var(--fg-muted)",
            border: "none",
            borderBottom: panelTab === key ? "2px solid var(--brand)" : "2px solid transparent",
            cursor: "pointer",
          }}>
            {label}
          </button>
        ))}
      </div>

      {/* Scrollable content */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {aLoading && (
          <div style={{ padding: "20px", textAlign: "center", color: "#475569", fontSize: 12 }}>
            Computing <Tip term="factor graph">factor graph</Tip>…
          </div>
        )}
        {aError && <div style={{ padding: "12px", color: "#f87171", fontSize: 11 }}>⚠ {aError}</div>}

        {!aLoading && panelTab === "scores" && topScores.map((s) => (
          <ScoreCard key={s.entity_id} score={s}
            onSolution={() => onSolutionRequest(s)}
            onHighlight={() => onHighlight(s.entity_id)} />
        ))}
        {!aLoading && panelTab === "scores" && topScores.length === 0 && !aError && (
          <div style={{ padding: "20px 14px", color: "#475569", fontSize: 12 }}>
            {summary?.tile_count === 0
              ? "Click \"Fetch Heatmap\" to load FortyGuard temperature data."
              : "No scoreable entities in this viewport."}
          </div>
        )}

        {!aLoading && panelTab === "compound" && compounds.map((c, i) => (
          <CompoundCard
            key={`${c.emitter_id}-${c.receptor_id}`}
            compound={c}
            rank={i + 1}
            active={activeCompound?.emitter_id === c.emitter_id && activeCompound?.receptor_id === c.receptor_id}
            onClick={() => { onCompoundClick(c); onHighlight(c.receptor_id); }}
            onSolution={() => onCompoundSolution(c)}
          />
        ))}
        {!aLoading && panelTab === "compound" && compounds.length === 0 && !aError && (
          <div style={{ padding: "20px 14px", color: "#475569", fontSize: 12 }}>
            {summary?.tile_count === 0
              ? "Click \"Fetch Heatmap\" to load FortyGuard temperature data."
              : "No cross-silo compound risks in this viewport (500m radius, receptor ≥ 20)."}
          </div>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Score card
// ---------------------------------------------------------------------------
const ENTITY_ICONS_LOCAL: Record<string, string> = {
  school: "🎓", hospital: "🏥", shelter: "🏠", tree_canopy: "🌳",
  cooling_center: "❄️", fire_station: "🚒", capital_project: "🏗️",
  construction_permit: "🚧", nycha_development: "🏢", subway_station: "🚇",
  hvi_zone: "🌡️",
};

interface ForecastPoint { hour_offset: number; time_local: string; temperature_f: number; risk_score: number; wbgt_f: number; }
interface SatelliteData {
  segments: Record<string, number>;
  original_image_b64: string | null;
  segmented_image_b64: string | null;
  image_year: number | null;
  heat_driver: string;
  _mock?: boolean;
  _cached?: boolean;
}
interface StreetviewData {
  front: {
    original_image: string | null;
    segmented_image: string | null;
    segments: Record<string, number>;
    image_date: string | null;
  };
  _mock?: boolean;
  _cached?: boolean;
}

function ScoreCard({ score, onSolution, onHighlight }: { score: EntityScore; onSolution: () => void; onHighlight?: () => void }) {
  const [open,         setOpen]        = useState(false);
  const [forecast,     setForecast]    = useState<{ forecasts: ForecastPoint[]; recommendation: string } | null>(null);
  const [satellite,    setSatellite]   = useState<SatelliteData | null>(null);
  const [streetview,   setStreetview]  = useState<StreetviewData | null>(null);
  const [heatIntelPdf, setHeatIntelPdf] = useState<string | null>(null);   // base64 PDF
  const [pdfLoading,   setPdfLoading]  = useState(false);
  const [imgTab,       setImgTab]      = useState<"satellite" | "street">("satellite");
  const color = scoreColor(score.risk_score);

  useEffect(() => {
    if (!open) return;

    // 1. 6-hour risk forecast (FortyGuard tile + diurnal model)
    fetch(`${API_BASE}/api/forecast?entity_id=${encodeURIComponent(score.entity_id)}&hours_ahead=6`)
      .then((r) => r.json()).then(setForecast).catch(() => {});

    // 2. Satellite land-cover — live FortyGuard /v1/satellite (replaces 2015 tree census)
    fetch(`${API_BASE}/api/satellite`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latitude: score.lat, longitude: score.lon, date_str: "2024-08-23", time_str: "14:00" }),
    })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d && d.segments) setSatellite(d); })
      .catch(() => {});

    // 3. Street View segmentation — FortyGuard /v1/streetview
    fetch(`${API_BASE}/api/streetview`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latitude: score.lat, longitude: score.lon }),
    })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d && d.front) setStreetview(d); })
      .catch(() => {});
  }, [open, score.entity_id, score.lat, score.lon]);

  function fetchHeatIntel() {
    setPdfLoading(true);
    fetch(`${API_BASE}/api/heat-intelligence`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        latitude: score.lat, longitude: score.lon,
        temperature_f: score.temperature_f,
        date_str: "2024-08-23",
      }),
    })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.pdf_base64) setHeatIntelPdf(d.pdf_base64); })
      .catch(() => {})
      .finally(() => setPdfLoading(false));
  }

  return (
    <div style={{
      padding: "8px 12px", cursor: "pointer",
      borderBottom: "1px solid var(--border)",
      background: open ? "var(--bg-muted)" : "transparent",
    }}>
      <div onClick={() => { setOpen((v) => !v); onHighlight?.(); }} style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 14 }}>{ENTITY_ICONS_LOCAL[score.entity_type] ?? "📍"}</span>
        <span style={{ flex: 1, fontSize: 11, fontWeight: 600, color: "var(--fg)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {score.entity_name}
        </span>
        {/* Option B: data-source indicator — green = real FortyGuard env_params used */}
        <span style={{ fontSize: 8, color: score.env_source === "fortyguard_api_cached" ? "#22c55e" : "var(--fg-faint)" }}
          title={score.env_source === "fortyguard_api_cached" ? "Risk computed with real FortyGuard env_params" : "Risk computed with estimated env_params"}>
          ●
        </span>
        <span style={{ fontSize: 12, fontWeight: 700, color, minWidth: 36, textAlign: "right" }}>
          {score.risk_score}
        </span>
      </div>
      <div style={{ marginTop: 4, height: 2.5, background: "var(--border)", borderRadius: 2 }}>
        <div style={{ width: `${score.risk_score}%`, height: "100%", background: color, borderRadius: 2, transition: "width 0.3s" }} />
      </div>
      <div style={{ marginTop: 3, fontSize: 10, color: "var(--fg-muted)" }}>
        {score.department.split("(")[0].trim()} · {score.temperature_f.toFixed(1)}°F (FortyGuard LTM)
      </div>
      {open && (
        <>
          <div style={{ marginTop: 6, fontSize: 10, color: "var(--fg-muted)", lineHeight: 1.5 }}>
            {score.explanation}
            <div style={{ marginTop: 4, color: "var(--fg-subtle)", fontStyle: "italic", fontSize: 9 }}>{score.data_source}</div>
          </div>

          {/* FortyGuard environmental parameters grid */}
          {(score.wbgt_f || score.aqi) && (
            <div style={{ marginTop: 8, padding: 8, background: "var(--bg-muted)", borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 9, color: "var(--brand)", fontWeight: 700, marginBottom: 6 }}>
                🌡️ FORTYGUARD ENVIRONMENTAL PARAMETERS
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "3px 8px" }}>
                {([
                  ["Temperature",  `${score.temperature_f.toFixed(1)}°F`],
                  ["Heat Index",   score.heat_index_f ? `${score.heat_index_f.toFixed(1)}°F` : "—"],
                  ["WBGT",         score.wbgt_f ? `${score.wbgt_f.toFixed(1)}°F` : "—"],
                  ["Humidity",     score.humidity ? `${score.humidity.toFixed(0)}%` : "—"],
                  ["AQI",          score.aqi ? `${score.aqi.toFixed(0)}` : "—"],
                  ["PM2.5",        score.pm25 ? `${score.pm25.toFixed(1)} μg/m³` : "—"],
                  ["Solar",        score.solar_irradiance ? `${score.solar_irradiance.toFixed(0)} W/m²` : "—"],
                  ["NO₂",          score.no2 ? `${score.no2.toFixed(1)} ppb` : "—"],
                ] as [string, string][]).map(([label, val]) => (
                  <div key={label} style={{ display: "flex", justifyContent: "space-between", fontSize: 9 }}>
                    <span style={{ color: "var(--fg-muted)" }}>{label}</span>
                    <span style={{ color: "var(--fg)", fontWeight: 600 }}>{val}</span>
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 8, color: "var(--fg-subtle)", marginTop: 5 }}>
                Source: FortyGuard API · {score.env_params_source || "estimated"} · NYC Aug baseline
              </div>
            </div>
          )}

          {/* FortyGuard satellite + street view imagery tabs */}
          {(satellite || streetview) && (
            <div style={{ marginTop: 8, background: "var(--bg-muted)", borderRadius: 6, overflow: "hidden", border: "1px solid var(--border)" }}>
              {/* Tab switcher */}
              <div style={{ display: "flex", borderBottom: "1px solid var(--border)" }}>
                {satellite && (
                  <button onClick={() => setImgTab("satellite")} style={{
                    flex: 1, padding: "4px 0", fontSize: 9, fontWeight: 700, border: "none",
                    background: "transparent",
                    color: imgTab === "satellite" ? "#16a34a" : "var(--fg-muted)", cursor: "pointer",
                    borderBottom: imgTab === "satellite" ? "2px solid #16a34a" : "2px solid transparent",
                  }}>🛰️ SATELLITE</button>
                )}
                {streetview && (
                  <button onClick={() => setImgTab("street")} style={{
                    flex: 1, padding: "4px 0", fontSize: 9, fontWeight: 700, border: "none",
                    background: "transparent",
                    color: imgTab === "street" ? "var(--brand)" : "var(--fg-muted)", cursor: "pointer",
                    borderBottom: imgTab === "street" ? "2px solid var(--brand)" : "2px solid transparent",
                  }}>🏙️ STREET VIEW</button>
                )}
              </div>

              <div style={{ padding: 8 }}>
                {/* ── Satellite tab ── */}
                {imgTab === "satellite" && satellite && (
                  <>
                    <div style={{ fontSize: 8, color: "var(--fg-subtle)", marginBottom: 5 }}>
                      FortyGuard /v1/satellite · land-cover{satellite.image_year ? ` ${satellite.image_year}` : ""}
                      {satellite._mock && " · mock"}{satellite._cached && " · cached"}
                    </div>
                    {satellite.segmented_image_b64 && (
                      <img src={`data:image/png;base64,${satellite.segmented_image_b64}`}
                        alt="Land cover segmentation"
                        style={{ width: "100%", borderRadius: 4, marginBottom: 6 }} />
                    )}
                    {satellite.original_image_b64 && !satellite.segmented_image_b64 && (
                      <img src={`data:image/png;base64,${satellite.original_image_b64}`}
                        alt="Satellite view"
                        style={{ width: "100%", borderRadius: 4, marginBottom: 6 }} />
                    )}
                    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                      {Object.entries(satellite.segments).sort(([,a],[,b])=>b-a).map(([cls,pct]) => {
                        const isGreen = cls.toLowerCase().includes("tree") || cls.toLowerCase().includes("grass");
                        return (
                          <div key={cls} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                            <span style={{ fontSize: 8, color: "var(--fg-subtle)", width: 90, textTransform: "capitalize", flexShrink: 0 }}>
                              {cls.replace(/_/g," ")}
                            </span>
                            <div style={{ flex: 1, height: 4, background: "var(--border)", borderRadius: 2 }}>
                              <div style={{ width: `${Math.min(pct,100)}%`, height: "100%", borderRadius: 2,
                                background: isGreen ? "#22c55e" : "var(--fg-subtle)" }} />
                            </div>
                            <span style={{ fontSize: 8, color: "var(--fg-subtle)", width: 26, textAlign: "right" }}>{pct.toFixed(0)}%</span>
                          </div>
                        );
                      })}
                    </div>
                    {satellite.heat_driver && (
                      <div style={{ fontSize: 9, color: "var(--fg-muted)", marginTop: 6, fontStyle: "italic", lineHeight: 1.4 }}>
                        {satellite.heat_driver}
                      </div>
                    )}
                  </>
                )}

                {/* ── Street View tab ── */}
                {imgTab === "street" && streetview && (
                  <>
                    <div style={{ fontSize: 8, color: "var(--fg-subtle)", marginBottom: 5 }}>
                      FortyGuard /v1/streetview · ground-level segmentation
                      {streetview.front?.image_date && ` · ${streetview.front.image_date}`}
                      {streetview._mock && " · mock"}{streetview._cached && " · cached"}
                    </div>
                    {streetview.front?.segmented_image && (
                      <img src={`data:image/png;base64,${streetview.front.segmented_image}`}
                        alt="Street view segmentation"
                        style={{ width: "100%", borderRadius: 4, marginBottom: 6 }} />
                    )}
                    {streetview.front?.original_image && !streetview.front?.segmented_image && (
                      <img src={`data:image/png;base64,${streetview.front.original_image}`}
                        alt="Street view"
                        style={{ width: "100%", borderRadius: 4, marginBottom: 6 }} />
                    )}
                    {streetview.front?.segments && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                        {Object.entries(streetview.front.segments).sort(([,a],[,b])=>b-a).map(([cls,pct]) => {
                          const isGreen = cls.toLowerCase().includes("tree") || cls.toLowerCase().includes("grass") || cls.toLowerCase().includes("vegetation");
                          return (
                            <div key={cls} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                              <span style={{ fontSize: 8, color: "var(--fg-subtle)", width: 90, flexShrink: 0 }}>{cls}</span>
                              <div style={{ flex: 1, height: 4, background: "var(--border)", borderRadius: 2 }}>
                                <div style={{ width: `${Math.min(pct,100)}%`, height: "100%", borderRadius: 2,
                                  background: isGreen ? "#22c55e" : "var(--brand)" }} />
                              </div>
                              <span style={{ fontSize: 8, color: "var(--fg-subtle)", width: 26, textAlign: "right" }}>{pct.toFixed(0)}%</span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {/* FortyGuard Heat Intelligence PDF report */}
          <div style={{ marginTop: 8 }}>
            {!heatIntelPdf && (
              <button
                onClick={(e) => { e.stopPropagation(); fetchHeatIntel(); }}
                disabled={pdfLoading}
                style={{
                  width: "100%", padding: "5px 0", borderRadius: 6, fontSize: 10,
                  fontWeight: 700, border: "1px solid var(--border-strong)",
                  background: pdfLoading ? "var(--bg-muted)" : "var(--bg-card)",
                  color: pdfLoading ? "var(--fg-subtle)" : "var(--brand)", cursor: pdfLoading ? "not-allowed" : "pointer",
                }}
              >
                {pdfLoading ? "⏳ Generating Heat Intelligence Report…" : "🔬 Get FortyGuard Heat Intelligence PDF"}
              </button>
            )}
            {heatIntelPdf && (
              <a
                href={`data:application/pdf;base64,${heatIntelPdf}`}
                download={`heat_intelligence_${score.entity_id}.pdf`}
                onClick={(e) => e.stopPropagation()}
                style={{
                  display: "block", width: "100%", padding: "5px 0", borderRadius: 6,
                  fontSize: 10, fontWeight: 700, textAlign: "center", textDecoration: "none",
                  border: "1px solid var(--brand)", background: "#eff6ff", color: "var(--brand)",
                }}
              >
                📄 Download Heat Intelligence Report (PDF)
              </a>
            )}
          </div>

          {/* 6-hour forecast mini chart */}
          {forecast && forecast.forecasts && (
            <div style={{ marginTop: 8, padding: 8, background: "var(--bg-muted)", borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 9, color: "var(--compound)", fontWeight: 700, marginBottom: 6 }}>
                📈 6-HOUR RISK FORECAST · FortyGuard + Diurnal Model
              </div>
              <div style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 44 }}>
                {forecast.forecasts.map((f) => {
                  const barHeight = Math.max(4, f.risk_score / 2);
                  const barColor = f.risk_score >= 70 ? "#ef4444" : f.risk_score >= 40 ? "#f59e0b" : "#22c55e";
                  return (
                    <div key={f.hour_offset} title={`${f.temperature_f}°F · WBGT ${f.wbgt_f}°F`} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
                      <div style={{
                        width: "100%", height: barHeight, background: barColor,
                        borderRadius: "2px 2px 0 0", transition: "height 0.3s",
                      }} />
                      <span style={{ fontSize: 7, color: "var(--fg-subtle)" }}>{f.time_local.split(":")[0]}</span>
                    </div>
                  );
                })}
              </div>
              <div style={{ fontSize: 9, color: "var(--fg-muted)", marginTop: 4, lineHeight: 1.4 }}>
                {forecast.recommendation}
              </div>
            </div>
          )}

          <button
            id="what-can-i-do-btn"
            onClick={(e) => { e.stopPropagation(); onSolution(); }}
            style={{
              marginTop: 8, width: "100%", padding: "5px 0", borderRadius: 6,
              fontSize: 11, fontWeight: 700, background: "var(--brand)", color: "#fff",
              border: "none", cursor: "pointer",
            }}
          >
            🟢 What can I do?
          </button>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compound risk card
// ---------------------------------------------------------------------------
function CompoundCard({ compound, rank, active, onClick, onSolution }: {
  compound: CompoundRisk; rank: number; active: boolean;
  onClick: () => void; onSolution: () => void;
}) {
  const color = scoreColor(compound.compound_score);
  return (
    <div
      onClick={onClick}
      style={{
        padding: "10px 12px", cursor: "pointer",
        borderBottom: "1px solid var(--border)",
        background:   active ? "#f5f3ff" : "transparent",
        borderLeft:   active ? "3px solid var(--compound)" : "3px solid transparent",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 10, color: "var(--fg-subtle)" }}>#{rank}</span>
        <span style={{ fontSize: 10, fontWeight: 700, color: "var(--compound)", marginLeft: "auto" }}>
          <Tip term="cross-silo">cross-silo</Tip>
        </span>
        <span style={{ fontSize: 11, fontWeight: 800, color }}>
          {compound.compound_score}/100
        </span>
      </div>

      <div style={{ display: "flex", gap: 4, alignItems: "flex-start", marginBottom: 2 }}>
        <span style={{ fontSize: 12 }}>🔺</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 11, color: "#b45309", fontWeight: 600, lineHeight: 1.3 }}>{compound.emitter_name}</div>
          <div style={{ fontSize: 10, color: "var(--fg-subtle)" }}>{compound.emitter_department.split("(")[0].trim()}</div>
        </div>
      </div>

      <div style={{ paddingLeft: 20, marginBottom: 2, fontSize: 10, color: "var(--fg-subtle)" }}>
        ↕ {compound.distance_m.toFixed(0)} m · +{compound.emitter_delta_t.toFixed(1)}°F added
      </div>

      <div style={{ display: "flex", gap: 4, alignItems: "flex-start", marginBottom: active ? 6 : 0 }}>
        <span style={{ fontSize: 12 }}>🔻</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 11, color: "#16a34a", fontWeight: 600, lineHeight: 1.3 }}>{compound.receptor_name}</div>
          <div style={{ fontSize: 10, color: "var(--fg-subtle)" }}>
            {compound.receptor_department.split("(")[0].trim()} · <Tip term="risk score">risk</Tip> {compound.receptor_risk_score}/100
          </div>
        </div>
      </div>

      {active && (
        <>
          <div style={{ fontSize: 10, color: "var(--fg-muted)", lineHeight: 1.5, marginTop: 4, padding: "6px 8px", background: "var(--bg-muted)", borderRadius: 4, border: "1px solid var(--border)" }}>
            {compound.insight}
          </div>
          <button
            onClick={(e) => { e.stopPropagation(); onSolution(); }}
            style={{
              marginTop: 8, width: "100%", padding: "5px 0", borderRadius: 6,
              fontSize: 11, fontWeight: 700, background: "var(--brand)", color: "#fff",
              border: "none", cursor: "pointer",
            }}
          >
            🟢 What can I do?
          </button>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Solution overlay — slides up from bottom
// ---------------------------------------------------------------------------
const EFFORT_COLORS = { low: "#22c55e", medium: "#f59e0b", high: "#f87171" };
const EFFORT_LABEL  = { low: "LOW EFFORT", medium: "MODERATE", high: "HIGH EFFORT" };

function SolutionOverlay({ solution, loading, onClose }: {
  solution: SolutionCard | null;
  loading: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div style={{
      position: "absolute", bottom: 0, left: 0, right: 0,
      height: "75%", background: "var(--bg-card)",
      border: "1px solid var(--border)",
      borderRadius: "14px 14px 0 0",
      display: "flex", flexDirection: "column",
      boxShadow: "0 -8px 40px rgba(0,0,0,0.12)",
      zIndex: 500,
      animation: "slideUp 0.25s ease",
    }}>
      <style>{`@keyframes slideUp { from { transform: translateY(40px); opacity: 0; } to { transform: none; opacity: 1; } }`}</style>

      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "10px 16px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div>
          <div style={{ color: "#16a34a", fontWeight: 700, fontSize: 13 }}>🟢 What can I do?</div>
          {solution && (
            <div style={{ color: "var(--fg-muted)", fontSize: 10, marginTop: 1 }}>
              {solution.entity_name}
              {solution.generated_by !== "fallback" && (
                <span style={{
                  marginLeft: 6, fontSize: 9, padding: "1px 5px", borderRadius: 8,
                  background: "#dcfce7", color: "#166534", border: "1px solid #86efac",
                }}>📡 Live · {solution.generated_by}</span>
              )}
              {solution.generated_by === "fallback" && (
                <span style={{
                  marginLeft: 6, fontSize: 9, padding: "1px 5px", borderRadius: 8,
                  background: "#fef3c7", color: "#92400e", border: "1px solid #fcd34d",
                }}>⚠️ Offline</span>
              )}
            </div>
          )}
        </div>
        <button onClick={onClose} style={{ color: "var(--fg-subtle)", background: "none", border: "none", fontSize: 18, cursor: "pointer" }}>✕</button>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px" }}>
        {loading && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 10 }}>
            <div style={{ fontSize: 28 }}>⚡</div>
            <div style={{ color: "var(--fg)", fontSize: 13, fontWeight: 600 }}>Generating recommendations…</div>
            <div style={{ color: "var(--fg-muted)", fontSize: 11 }}>Gemini is searching for active NYC programs</div>
          </div>
        )}

        {solution && !loading && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

            {/* What you can do */}
            <section>
              <h3 style={{ color: "#16a34a", fontSize: 11, fontWeight: 700, letterSpacing: 1, margin: "0 0 8px" }}>
                ACTIONS
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {solution.what_you_can_do.map((item, i) => (
                  <div key={i} style={{
                    padding: "8px 10px", background: "var(--bg-muted)",
                    border: "1px solid var(--border)", borderRadius: 8,
                    display: "flex", gap: 10, alignItems: "flex-start",
                  }}>
                    <span style={{
                      fontSize: 9, fontWeight: 700, padding: "2px 5px", borderRadius: 4,
                      background: `${EFFORT_COLORS[item.effort]}20`,
                      color: EFFORT_COLORS[item.effort], flexShrink: 0, marginTop: 1,
                    }}>
                      {EFFORT_LABEL[item.effort]}
                    </span>
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--fg)" }}>{item.action}</div>
                      <div style={{ fontSize: 11, color: "var(--fg-muted)", marginTop: 2, lineHeight: 1.45 }}>{item.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* What's happening */}
            {solution.whats_happening.length > 0 && (
              <section>
                <h3 style={{ color: "var(--brand)", fontSize: 11, fontWeight: 700, letterSpacing: 1, margin: "0 0 8px" }}>
                  ACTIVE PROGRAMS
                </h3>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {solution.whats_happening.map((prog, i) => (
                    <div key={i} style={{ padding: "7px 10px", background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 8 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--brand)" }}>{prog.program}</div>
                      <div style={{ fontSize: 10, color: "var(--fg-subtle)" }}>{prog.agency}</div>
                      <div style={{ fontSize: 11, color: "var(--fg-muted)", marginTop: 2 }}>{prog.detail}</div>
                      {prog.url && (
                        <a href={prog.url} target="_blank" rel="noreferrer"
                          style={{ fontSize: 10, color: "var(--brand)", display: "block", marginTop: 3 }}>
                          {prog.url} →
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Optimistic outlook */}
            {solution.optimistic_outlook && (
              <section>
                <h3 style={{ color: "#b45309", fontSize: 11, fontWeight: 700, letterSpacing: 1, margin: "0 0 6px" }}>
                  IF WE ACT
                </h3>
                <p style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.55, margin: 0 }}>
                  {solution.optimistic_outlook}
                </p>
              </section>
            )}

            {/* Live context */}
            {solution.live_context && solution.generated_by !== "fallback" && (
              <section style={{ padding: "8px 10px", background: "#eff6ff", borderRadius: 8, border: "1px solid #bfdbfe" }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: "var(--brand)", letterSpacing: 1, marginBottom: 4 }}>📡 LIVE CONTEXT</div>
                <p style={{ fontSize: 11, color: "var(--brand)", lineHeight: 1.5, margin: 0 }}>{solution.live_context}</p>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
