/**
 * GuidedTour — 5-step overlay tour that runs on first visit.
 *
 * Each step highlights a UI element via a glowing ring and shows a tooltip
 * bubble. Triggered automatically once (localStorage: heatgraph_tour_done)
 * and restartable via the "? Tour" button in the header.
 *
 * No library dependency — pure React state + CSS.
 */
import { useState, useEffect, useCallback } from "react";

interface TourStep {
  target:   string;   // element id
  title:    string;
  body:     string;
  position: "top" | "bottom" | "left" | "right";
}

const TOUR_STEPS: TourStep[] = [
  {
    target:   "fetch-heatmap-btn",
    title:    "1 · Start here: Fetch the heat map",
    body:     "Click this to load FortyGuard's street-level temperature data for NYC. This is real 2-metre ambient air temperature — not weather-station averages. Takes ~10 seconds.",
    position: "bottom",
  },
  {
    target:   "role-selector",
    title:    "2 · Select your role",
    body:     "Choose the role that matches your work. The platform filters entities to what's relevant — a health officer sees shelters and hospitals; a school admin sees schools. Switch anytime.",
    position: "bottom",
  },
  {
    target:   "compound-tab",
    title:    "3 · Cross-silo risks are your most important alerts",
    body:     "These are places where two city agencies unknowingly affect each other through temperature. A construction project (Buildings Dept) near a shelter (Homeless Services) — neither agency sees this connection. HeatGraph does.",
    position: "left",
  },
  {
    target:   "what-can-i-do-btn",
    title:    "4 · Get specific actions",
    body:     "Every risk card has this button. Click it to get actions tailored to your role, active NYC programs you can contact, and what improves if you act.",
    position: "top",
  },
  {
    target:   "network-tab",
    title:    "5 · See the connections visually",
    body:     "Switch to Network view to see entities as a force-directed graph. Nodes are city entities, edges are compound heat links across departments. Hover to highlight connections. Click a node to get solutions.",
    position: "left",
  },
];

interface GuidedTourProps {
  active:   boolean;
  onClose:  () => void;
}

export function GuidedTour({ active, onClose }: GuidedTourProps) {
  const [step,    setStep]    = useState(0);
  const [rect,    setRect]    = useState<DOMRect | null>(null);

  const current = TOUR_STEPS[step];

  // Find target element position
  const measureTarget = useCallback(() => {
    if (!active) return;
    const el = document.getElementById(current.target);
    if (el) {
      setRect(el.getBoundingClientRect());
    } else {
      setRect(null);
    }
  }, [active, current.target]);

  useEffect(() => {
    measureTarget();
    window.addEventListener("resize", measureTarget);
    return () => window.removeEventListener("resize", measureTarget);
  }, [measureTarget]);

  if (!active) return null;

  const PADDING = 8;      // glow ring padding around the element
  const TIP_W   = 280;    // tooltip width in px

  function close() {
    try { localStorage.setItem("heatgraph_tour_done", "1"); } catch { /* */ }
    onClose();
  }

  function next() {
    if (step < TOUR_STEPS.length - 1) {
      setStep((s) => s + 1);
    } else {
      close();
    }
  }

  function prev() {
    if (step > 0) setStep((s) => s - 1);
  }

  // Tooltip position relative to the highlight ring
  function tooltipStyle(): React.CSSProperties {
    if (!rect) return { top: "50%", left: "50%", transform: "translate(-50%,-50%)" };

    const r = {
      top:    rect.top    - PADDING,
      left:   rect.left   - PADDING,
      right:  rect.right  + PADDING,
      bottom: rect.bottom + PADDING,
      width:  rect.width  + PADDING * 2,
      height: rect.height + PADDING * 2,
    };

    const base: React.CSSProperties = {
      position: "fixed",
      width:    TIP_W,
      zIndex:   10001,
    };

    switch (current.position) {
      case "bottom": return { ...base, top: r.bottom + 10, left: r.left + r.width / 2 - TIP_W / 2 };
      case "top":    return { ...base, bottom: window.innerHeight - r.top + 10, left: r.left + r.width / 2 - TIP_W / 2 };
      case "left":   return { ...base, top: r.top + r.height / 2 - 80, right: window.innerWidth - r.left + 10 };
      case "right":  return { ...base, top: r.top + r.height / 2 - 80, left: r.right + 10 };
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)",
        zIndex: 9999, pointerEvents: "none",
      }} />

      {/* Highlight ring around target */}
      {rect && (
        <div style={{
          position: "fixed",
          top:    rect.top    - PADDING,
          left:   rect.left   - PADDING,
          width:  rect.width  + PADDING * 2,
          height: rect.height + PADDING * 2,
          borderRadius: 8,
          boxShadow: "0 0 0 3px #f59e0b, 0 0 0 6px rgba(245,158,11,0.3), 0 0 40px rgba(245,158,11,0.2)",
          zIndex: 10000,
          pointerEvents: "none",
          animation: "tourPulse 1.8s ease-in-out infinite",
        }} />
      )}

      <style>{`
        @keyframes tourPulse {
          0%, 100% { box-shadow: 0 0 0 3px #f59e0b, 0 0 0 6px rgba(245,158,11,0.3); }
          50%       { box-shadow: 0 0 0 3px #f59e0b, 0 0 0 12px rgba(245,158,11,0.15); }
        }
      `}</style>

      {/* Tooltip bubble */}
      <div style={{
        ...tooltipStyle(),
        background: "#0f172a",
        border: "1px solid #f59e0b",
        borderRadius: 10,
        padding: "14px 16px",
        boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
        pointerEvents: "auto",
      }}>
        {/* Progress dots */}
        <div style={{ display: "flex", gap: 5, marginBottom: 10 }}>
          {TOUR_STEPS.map((_, i) => (
            <div key={i} style={{
              width: 6, height: 6, borderRadius: "50%",
              background: i === step ? "#f59e0b" : "#1e293b",
              border: "1px solid #334155",
            }} />
          ))}
        </div>

        <div style={{ fontSize: 12, fontWeight: 700, color: "#f59e0b", marginBottom: 6 }}>
          {current.title}
        </div>
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          {current.body}
        </div>

        {/* Buttons */}
        <div style={{ display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end" }}>
          <button onClick={close} style={{
            fontSize: 10, padding: "4px 10px", borderRadius: 6,
            background: "transparent", color: "#475569",
            border: "1px solid #1e293b", cursor: "pointer",
          }}>
            Skip tour
          </button>
          {step > 0 && (
            <button onClick={prev} style={{
              fontSize: 10, padding: "4px 10px", borderRadius: 6,
              background: "transparent", color: "#94a3b8",
              border: "1px solid #334155", cursor: "pointer",
            }}>
              ← Back
            </button>
          )}
          <button onClick={next} style={{
            fontSize: 11, fontWeight: 700, padding: "5px 14px", borderRadius: 6,
            background: "#c2410c", color: "#fff",
            border: "none", cursor: "pointer",
          }}>
            {step < TOUR_STEPS.length - 1 ? "Next →" : "Done ✓"}
          </button>
        </div>
      </div>
    </>
  );
}

/** Check if tour should auto-start (first visit). */
export function shouldAutoStartTour(): boolean {
  try { return !localStorage.getItem("heatgraph_tour_done"); } catch { return false; }
}
