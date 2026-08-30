/**
 * NetworkGraph — Cytoscape.js force-directed visualization of the compound
 * risk subgraph.
 *
 * Shows ONLY entities involved in at least one compound risk, filtered by
 * the active user role. The map shows everything; this graph shows the
 * interesting cross-silo connections.
 *
 * Interactions (Obsidian-style):
 *  - Hover node → dim all except that node + direct neighbors
 *  - Click node → show tooltip with name / type / score / temp
 *  - "What can I do?" → trigger Gemini solution call (onSolutionRequest)
 *  - Click on map → pan map to entity location (onNodeClick)
 *  - Department filter chips → hide/show by department
 *  - Scroll → zoom in/out
 *  - Drag → reposition nodes
 */
import { useEffect, useRef, useState, useCallback } from "react";
import cytoscape from "cytoscape";

export interface NetworkNodeData {
  id:               string;
  label:            string;
  entity_type:      string;
  role:             string;
  risk_score:       number;
  temperature_f:    number;
  department:       string;
  explanation:      string;
  data_source:      string;
  connection_count: number;
  icon:             string;
  color:            string;
  border_color:     string;
  size:             number;
  lat:              number;
  lon:              number;
}

interface NetworkGraphProps {
  city:                string;
  role:                string;
  highlightedEntityId?: string | null;
  onNodeClick?:        (entityId: string, lat: number, lon: number) => void;
  onSolutionRequest?:  (node: NetworkNodeData) => void;
}

// In production (served by FastAPI), use relative URLs.
// In dev, Vite proxy forwards /api/* to localhost:8000.
const API_BASE = "";

function scoreColor(score: number): string {
  if (score >= 70) return "#ef4444";
  if (score >= 40) return "#f59e0b";
  return "#22c55e";
}

export function NetworkGraph({ city, role, highlightedEntityId, onNodeClick, onSolutionRequest }: NetworkGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef        = useRef<cytoscape.Core | null>(null);

  const [loading,      setLoading]      = useState(true);
  const [summary,      setSummary]      = useState<{ node_count: number; edge_count: number; departments: Record<string, number>; role_filter: string } | null>(null);
  const [selectedNode, setSelectedNode] = useState<NetworkNodeData | null>(null);
  const [showDetails,  setShowDetails]  = useState(false);
  const [deptFilters,  setDeptFilters]  = useState<Record<string, boolean>>({});
  const [minRiskFilter, setMinRiskFilter] = useState(0);
  const [error,        setError]        = useState<string | null>(null);

  // ── Fetch graph data ──────────────────────────────────────────────────────
  useEffect(() => {
    setLoading(true);
    setError(null);
    setSelectedNode(null);

    fetch(`${API_BASE}/api/network?city=${city}&role=${role}&node_limit=50`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        setSummary(data.summary);

        // Init department filters — all on
        const depts: Record<string, boolean> = {};
        for (const dept of Object.keys(data.summary?.departments ?? {})) {
          depts[dept] = true;
        }
        setDeptFilters(depts);

        renderGraph(data.elements);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [city, role]);

  // ── Cytoscape mount ───────────────────────────────────────────────────────
  const renderGraph = useCallback((elements: { nodes: { data: NetworkNodeData }[]; edges: { data: Record<string, unknown> }[] }) => {
    if (!containerRef.current) return;
    if (cyRef.current) cyRef.current.destroy();

    const allNodes = elements.nodes.map((n) => ({ data: n.data }));
    const allEdges = elements.edges.map((e) => ({ data: e.data }));

    const cy = cytoscape({
      container: containerRef.current,
      elements: [...allNodes, ...allEdges],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      style: [
        {
          selector: "node",
          style: {
            label:              "data(label)",
            "font-size":        9,
            color:              "#cbd5e1",
            "text-valign":      "bottom",
            "text-margin-y":    6,
            "text-max-width":   80,
            "text-wrap":        "ellipsis",
            "background-color": "data(color)",
            "border-color":     "data(border_color)",
            "border-width":     3,
            width:              "data(size)" as unknown as number,
            height:             "data(size)" as unknown as number,
          },
        },
        {
          selector: "edge",
          style: {
            width:         "data(width)" as unknown as number,
            "line-color":  "data(color)",
            "curve-style": "bezier",
            opacity:       0.55,
          },
        },
        {
          selector: ".dimmed",
          style: { opacity: 0.08 },
        },
        {
          selector: ".highlighted",
          style: { opacity: 1 },
        },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ] as any[],
      layout: {
        name:             "cose",
        animate:          true,
        animationDuration: 800,
        nodeRepulsion:    () => 8000,
        idealEdgeLength:  () => 120,
        gravity:          0.3,
        padding:          30,
        randomize:        false,
      } as cytoscape.LayoutOptions,
    });

    // Hover: Obsidian-style highlight
    cy.on("mouseover", "node", (evt) => {
      const node         = evt.target;
      const neighborhood = node.neighborhood().add(node);
      cy.elements().addClass("dimmed");
      neighborhood.removeClass("dimmed").addClass("highlighted");
    });
    cy.on("mouseout", "node", () => {
      cy.elements().removeClass("dimmed highlighted");
    });

    // Click: show tooltip + sync map
    cy.on("tap", "node", (evt) => {
      const data = evt.target.data() as NetworkNodeData;
      setSelectedNode(data);
      setShowDetails(false);
      onNodeClick?.(data.id, data.lat, data.lon);
    });

    // Click background: deselect
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        setSelectedNode(null);
        setShowDetails(false);
      }
    });

    cyRef.current = cy;
  }, [onNodeClick]);

  // ── External highlight (from list panel clicking a risk card) ────────────
  useEffect(() => {
    if (!cyRef.current || !highlightedEntityId) return;
    const node = cyRef.current.getElementById(highlightedEntityId);
    if (node.length === 0) return;
    cyRef.current.elements().addClass("dimmed");
    node.neighborhood().add(node).removeClass("dimmed").addClass("highlighted");
    cyRef.current.animate({ center: { eles: node }, zoom: 2 } as Parameters<typeof cyRef.current.animate>[0], { duration: 400 });
  }, [highlightedEntityId]);

  // ── Risk score filter ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!cyRef.current) return;
    cyRef.current.nodes().forEach((node) => {
      const visible = node.data("risk_score") >= minRiskFilter;
      node.style("display", visible ? "element" : "none");
    });
    cyRef.current.edges().forEach((edge) => {
      const srcHidden = edge.source().style("display") === "none";
      const tgtHidden = edge.target().style("display") === "none";
      edge.style("display", srcHidden || tgtHidden ? "none" : "element");
    });
  }, [minRiskFilter]);

  // ── Department filter toggle ──────────────────────────────────────────────
  function toggleDept(dept: string) {
    setDeptFilters((prev) => {
      const next = { ...prev, [dept]: !prev[dept] };
      if (cyRef.current) {
        cyRef.current.nodes().forEach((node) => {
          node.style("display", next[node.data("department")] === false ? "none" : "element");
        });
      }
      return next;
    });
  }

  const deptList = Object.keys(deptFilters);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "#080b14", position: "relative" }}>

      {/* ── Color legend ─────────────────────────────────────────────────── */}
      <div style={{
        padding: "5px 10px", display: "flex", gap: 12, fontSize: 9,
        color: "#64748b", borderBottom: "1px solid #1e293b", flexShrink: 0,
        flexWrap: "wrap", alignItems: "center",
      }}>
        <span style={{ fontWeight: 700, color: "#475569" }}>NODE:</span>
        <span><span style={{ color: "#f97316" }}>●</span> Emitter</span>
        <span><span style={{ color: "#ef4444" }}>●</span> Receptor</span>
        <span><span style={{ color: "#22c55e" }}>●</span> Sink</span>
        <span><span style={{ color: "#8b5cf6" }}>●</span> Both</span>
        <span style={{ marginLeft: 4, color: "#334155" }}>│</span>
        <span style={{ fontWeight: 700, color: "#475569" }}>EDGE:</span>
        <span><span style={{ color: "#ef4444" }}>—</span> High</span>
        <span><span style={{ color: "#f59e0b" }}>—</span> Med</span>
        <span><span style={{ color: "#6b7280" }}>—</span> Low</span>
      </div>

      {/* ── Risk filter + dept chips ──────────────────────────────────────── */}
      <div style={{
        padding: "5px 10px", display: "flex", gap: 5, flexWrap: "wrap",
        borderBottom: "1px solid #1e293b", flexShrink: 0, alignItems: "center",
      }}>
        <span style={{ color: "#475569", fontSize: 9, fontWeight: 700 }}>MIN RISK:</span>
        {([0, 30, 50, 70] as const).map((t) => (
          <button key={t} onClick={() => setMinRiskFilter(t)} style={{
            fontSize: 9, padding: "2px 6px", borderRadius: 10,
            background: minRiskFilter === t ? "#334155" : "transparent",
            color: minRiskFilter === t ? "#e2e8f0" : "#475569",
            border: "1px solid #1e293b", cursor: "pointer",
          }}>
            {t === 0 ? "All" : `${t}+`}
          </button>
        ))}
        <span style={{ color: "#1e293b", fontSize: 9, margin: "0 4px" }}>│</span>
        <span style={{ color: "#475569", fontSize: 9, fontWeight: 700 }}>DEPTS:</span>
        {deptList.map((dept) => {
          const shortName = dept.split("(")[0].replace("NYC ", "").trim();
          const count     = summary?.departments?.[dept] ?? 0;
          const on        = deptFilters[dept] !== false;
          return (
            <button key={dept} onClick={() => toggleDept(dept)} title={dept} style={{
              fontSize: 9, padding: "2px 6px", borderRadius: 10,
              border: "1px solid #1e293b",
              background: on ? "#1e293b" : "transparent",
              color: on ? "#94a3b8" : "#334155",
              cursor: "pointer",
            }}>
              {shortName} ({count})
            </button>
          );
        })}
      </div>

      {/* ── Summary bar ──────────────────────────────────────────────────── */}
      {summary && (
        <div style={{ padding: "4px 10px", fontSize: 10, color: "#475569", borderBottom: "1px solid #1e293b", flexShrink: 0 }}>
          {summary.node_count} entities · {summary.edge_count} compound links · role: {summary.role_filter}
          {summary.node_count === 0 && (
            <span style={{ color: "#f59e0b", marginLeft: 6 }}>
              ⚠ No compound risks found — try fetching the heatmap first
            </span>
          )}
        </div>
      )}

      {/* ── Cytoscape canvas ─────────────────────────────────────────────── */}
      <div ref={containerRef} style={{ flex: 1, minHeight: 0 }} />

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {error && (
        <div style={{ position: "absolute", top: 80, left: 16, right: 16, background: "#2d1515", border: "1px solid #7f1d1d", borderRadius: 8, padding: "10px 14px", fontSize: 12, color: "#fca5a5" }}>
          ⚠ Network graph error: {error}
        </div>
      )}

      {/* ── Loading overlay ───────────────────────────────────────────────── */}
      {loading && (
        <div style={{
          position: "absolute", inset: 0, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          background: "rgba(8,11,20,0.85)", color: "#94a3b8", fontSize: 13, gap: 10,
        }}>
          <div style={{ fontSize: 24 }}>🕸️</div>
          Building network graph…
        </div>
      )}

      {/* ── Node tooltip (fixed at bottom of panel) ──────────────────────── */}
      {selectedNode && (
        <div style={{
          position: "absolute", bottom: 12, left: 12, right: 12,
          background: "#0f172a",
          border: `1px solid ${scoreColor(selectedNode.risk_score)}40`,
          borderLeft: `3px solid ${scoreColor(selectedNode.risk_score)}`,
          borderRadius: 10,
          padding: "12px 14px",
          boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
          zIndex: 10,
        }}>
          {/* Header row */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#f1f5f9", lineHeight: 1.3 }}>
                {selectedNode.icon} {selectedNode.label}
              </div>
              <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>
                {selectedNode.entity_type.replace(/_/g, " ")} · {selectedNode.department.split("(")[0].trim()}
              </div>
            </div>
            <div style={{ textAlign: "right", flexShrink: 0 }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: scoreColor(selectedNode.risk_score), lineHeight: 1 }}>
                {selectedNode.risk_score}
              </div>
              <div style={{ fontSize: 9, color: "#475569" }}>/100</div>
            </div>
          </div>

          {/* Stats row */}
          <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 10, color: "#64748b" }}>
            <span>🌡️ {selectedNode.temperature_f.toFixed(1)}°F</span>
            <span>🔗 {selectedNode.connection_count} connections</span>
            <span style={{ color: selectedNode.role === "emitter" ? "#f97316" : selectedNode.role === "sink" ? "#22c55e" : "#ef4444" }}>
              ● {selectedNode.role}
            </span>
          </div>

          {/* Detail expansion */}
          {showDetails && (
            <div style={{ marginTop: 10, padding: "8px 10px", background: "#080b14", borderRadius: 6, fontSize: 10, color: "#94a3b8", lineHeight: 1.55 }}>
              <div style={{ fontWeight: 700, color: "#3b82f6", fontSize: 9, letterSpacing: 1, marginBottom: 4 }}>FACTOR COMPUTATION</div>
              {selectedNode.explanation}
              <div style={{ marginTop: 4, fontSize: 9, color: "#374151", fontStyle: "italic" }}>{selectedNode.data_source}</div>
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button
              onClick={() => onSolutionRequest?.(selectedNode)}
              style={{
                flex: 2, padding: "7px 0", borderRadius: 7, fontSize: 11, fontWeight: 700,
                background: "#065f46", color: "#6ee7b7",
                border: "1px solid #047857", cursor: "pointer",
              }}
            >
              🟢 What can I do?
            </button>
            <button
              onClick={() => setShowDetails((v) => !v)}
              style={{
                flex: 1, padding: "7px 0", borderRadius: 7, fontSize: 11,
                background: "transparent", color: "#64748b",
                border: "1px solid #1e293b", cursor: "pointer",
              }}
            >
              {showDetails ? "Less" : "Details"}
            </button>
            <button
              onClick={() => setSelectedNode(null)}
              style={{
                padding: "7px 10px", borderRadius: 7, fontSize: 11,
                background: "transparent", color: "#374151",
                border: "1px solid #1e293b", cursor: "pointer",
              }}
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
