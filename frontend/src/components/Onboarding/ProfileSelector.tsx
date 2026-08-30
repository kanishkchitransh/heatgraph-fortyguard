/**
 * ProfileSelector — full-screen onboarding MCQ.
 *
 * Shows once on first visit (localStorage key "heatgraph_profile").
 * Fades out after selection and calls onComplete with the chosen profile.
 *
 * Implements Norman's principles:
 *  - Affordance: large clickable cards, not small chips
 *  - Mapping: real-world role → platform experience
 *  - Feedback: personalized welcome after selection
 */
import { useState } from "react";

export interface UserProfile {
  primary_role:          "planner" | "health" | "schools" | "infrastructure" | "community" | "multi";
  selected_domains?:     string[];
  entity_types_visible:  string[];
  created_at:            string;
}

interface ProfileSelectorProps {
  onComplete: (profile: UserProfile) => void;
}

const ROLES_CONFIG = [
  {
    key:          "planner" as const,
    icon:         "🏗️",
    title:        "City Planner",
    description:  "I coordinate across departments and need to see how projects interact citywide.",
    entity_types: [],   // null = all
  },
  {
    key:          "health" as const,
    icon:         "🏥",
    title:        "Health Officer",
    description:  "I protect public health and need to see where heat hits vulnerable people.",
    entity_types: ["hospital", "shelter", "hvi_zone", "nycha_development", "cooling_center"],
  },
  {
    key:          "schools" as const,
    icon:         "🎓",
    title:        "School District",
    description:  "I manage schools and need to know which buildings are at heat risk.",
    entity_types: ["school", "construction_permit", "capital_project"],
  },
  {
    key:          "infrastructure" as const,
    icon:         "⚡",
    title:        "Infrastructure",
    description:  "I manage transit, utilities, or public works and need to see thermal stress.",
    entity_types: ["subway_station", "capital_project", "construction_permit"],
  },
  {
    key:          "community" as const,
    icon:         "🏘️",
    title:        "Community Member",
    description:  "I live here and want to understand heat on my block and what I can do about it.",
    entity_types: ["school", "shelter", "tree_canopy", "cooling_center", "hvi_zone", "nycha_development"],
  },
  {
    key:          "multi" as const,
    icon:         "🔍",
    title:        "I wear multiple hats",
    description:  "My work spans more than one area. Show me everything.",
    entity_types: [],
  },
];

const DOMAIN_OPTIONS = [
  { key: "planning",  label: "Urban planning / capital projects" },
  { key: "health",    label: "Public health / emergency services" },
  { key: "education", label: "Education / school facilities" },
  { key: "transit",   label: "Infrastructure / transit / utilities" },
  { key: "community", label: "Community organizing / advocacy" },
  { key: "environment", label: "Environment / parks / sustainability" },
];

const DOMAIN_ENTITY_MAP: Record<string, string[]> = {
  planning:    ["capital_project", "construction_permit", "hvi_zone"],
  health:      ["hospital", "shelter", "hvi_zone", "cooling_center", "nycha_development"],
  education:   ["school", "construction_permit"],
  transit:     ["subway_station", "capital_project"],
  community:   ["school", "shelter", "tree_canopy", "cooling_center", "hvi_zone"],
  environment: ["tree_canopy", "cooling_center", "hvi_zone"],
};

export function ProfileSelector({ onComplete }: ProfileSelectorProps) {
  const [step,    setStep]    = useState<"pick" | "multi">("pick");
  const [domains, setDomains] = useState<Record<string, boolean>>({});
  const [exiting, setExiting] = useState(false);

  function handleRoleSelect(role: typeof ROLES_CONFIG[number]) {
    if (role.key === "multi") {
      setStep("multi");
      return;
    }
    commit({
      primary_role:         role.key,
      entity_types_visible: role.entity_types,
      created_at:           new Date().toISOString(),
    });
  }

  function handleMultiCommit() {
    const selected = Object.entries(domains).filter(([, v]) => v).map(([k]) => k);
    const types    = [...new Set(selected.flatMap((d) => DOMAIN_ENTITY_MAP[d] ?? []))];
    commit({
      primary_role:         "multi",
      selected_domains:     selected,
      entity_types_visible: types,
      created_at:           new Date().toISOString(),
    });
  }

  function commit(profile: UserProfile) {
    try { localStorage.setItem("heatgraph_profile", JSON.stringify(profile)); } catch { /* blocked */ }
    setExiting(true);
    setTimeout(() => onComplete(profile), 350);
  }

  function toggleDomain(key: string) {
    setDomains((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <div
      style={{
        position:   "fixed",
        inset:      0,
        background: "#080b14",
        display:    "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding:    "24px 16px",
        zIndex:     1000,
        opacity:    exiting ? 0 : 1,
        transition: "opacity 0.35s ease",
        overflowY:  "auto",
      }}
    >
      {/* Brand */}
      <div style={{ marginBottom: 8, fontSize: 28 }}>🌡️</div>
      <h1 style={{ color: "#f1f5f9", fontSize: 26, fontWeight: 800, margin: 0, letterSpacing: -0.5 }}>
        ImpactGraph
      </h1>
      <p style={{ color: "#64748b", fontSize: 14, margin: "6px 0 0", textAlign: "center" }}>
        New York City's cross-departmental heat intelligence platform
      </p>

      {step === "pick" ? (
        <>
          <p style={{ color: "#94a3b8", fontSize: 15, fontWeight: 600, margin: "28px 0 16px", textAlign: "center" }}>
            Which best describes your role?
          </p>

          {/* 2×3 card grid */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 14,
            maxWidth: 700,
            width: "100%",
          }}>
            {ROLES_CONFIG.map((r) => (
              <RoleCard key={r.key} role={r} onClick={() => handleRoleSelect(r)} />
            ))}
          </div>
        </>
      ) : (
        /* Multi-select follow-up */
        <div style={{ maxWidth: 480, width: "100%", marginTop: 28 }}>
          <p style={{ color: "#94a3b8", fontSize: 15, fontWeight: 600, marginBottom: 16, textAlign: "center" }}>
            Which areas do you work across?
            <span style={{ display: "block", fontSize: 12, fontWeight: 400, color: "#64748b", marginTop: 4 }}>
              Select all that apply
            </span>
          </p>

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {DOMAIN_OPTIONS.map((opt) => (
              <label
                key={opt.key}
                style={{
                  display:    "flex",
                  alignItems: "center",
                  gap:        12,
                  padding:    "12px 16px",
                  background: domains[opt.key] ? "#1e3a5f" : "#0f172a",
                  border:     `1px solid ${domains[opt.key] ? "#3b82f6" : "#1e293b"}`,
                  borderRadius: 10,
                  cursor:     "pointer",
                  transition: "all 0.15s",
                }}
              >
                <input
                  type="checkbox"
                  checked={!!domains[opt.key]}
                  onChange={() => toggleDomain(opt.key)}
                  style={{ width: 16, height: 16, accentColor: "#3b82f6", cursor: "pointer" }}
                />
                <span style={{ color: "#e2e8f0", fontSize: 14 }}>{opt.label}</span>
              </label>
            ))}
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
            <button
              onClick={() => setStep("pick")}
              style={{
                flex: 1, padding: "11px 0", borderRadius: 10, fontSize: 14,
                background: "transparent", color: "#64748b",
                border: "1px solid #1e293b", cursor: "pointer",
              }}
            >
              ← Back
            </button>
            <button
              onClick={handleMultiCommit}
              disabled={!Object.values(domains).some(Boolean)}
              style={{
                flex: 2, padding: "11px 0", borderRadius: 10, fontSize: 14, fontWeight: 700,
                background: Object.values(domains).some(Boolean) ? "#e05c2e" : "#1e293b",
                color: Object.values(domains).some(Boolean) ? "#fff" : "#475569",
                border: "none", cursor: Object.values(domains).some(Boolean) ? "pointer" : "not-allowed",
                transition: "all 0.15s",
              }}
            >
              Show my ImpactGraph →
            </button>
          </div>
        </div>
      )}

      <p style={{ color: "#334155", fontSize: 11, marginTop: 24 }}>
        Your selection is saved locally and never shared. You can change it anytime.
      </p>
    </div>
  );
}

function RoleCard({ role, onClick }: { role: typeof ROLES_CONFIG[number]; onClick: () => void }) {
  const [hovered, setHovered] = useState(false);

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background:   hovered ? "#0f2040" : "#0c1220",
        border:       `1px solid ${hovered ? "#3b82f6" : "#1e293b"}`,
        borderRadius: 14,
        padding:      "20px 18px",
        cursor:       "pointer",
        textAlign:    "left",
        transition:   "all 0.15s",
        transform:    hovered ? "translateY(-2px)" : "none",
        boxShadow:    hovered ? "0 8px 24px rgba(59,130,246,0.12)" : "none",
      }}
    >
      <div style={{ fontSize: 28, marginBottom: 10 }}>{role.icon}</div>
      <div style={{ color: "#f1f5f9", fontSize: 15, fontWeight: 700, marginBottom: 6 }}>
        {role.title}
      </div>
      <div style={{ color: "#64748b", fontSize: 12, lineHeight: 1.5 }}>
        {role.description}
      </div>
    </button>
  );
}
