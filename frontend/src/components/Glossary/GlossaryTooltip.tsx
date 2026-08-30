/**
 * GlossaryTooltip — hover any domain keyword to see a plain-English explanation.
 *
 * Usage:
 *   <Tip term="compound risk">Compound Risk</Tip>
 *   <Tip term="emitter">emitter</Tip>
 *
 * Implements Norman's "knowledge in the world" principle:
 * the explanation is attached to the word, not buried in a manual.
 */
import { useState, useRef } from "react";

const GLOSSARY: Record<string, string> = {
  "emitter":
    "An entity that raises local temperature — like a construction site clearing trees or a building exhausting hot air onto the sidewalk. Think of it as a heat source.",
  "receptor":
    "An entity harmed by rising temperature — like a school without AC or a nursing home full of elderly residents. The hotter it gets, the higher their risk.",
  "sink":
    "An entity that cools the area around it — like a block of mature street trees or a green roof. They absorb and evaporate heat rather than radiating it.",
  "compound risk":
    "When a heat source (emitter) and a vulnerable place (receptor) are close together but managed by different city agencies. Neither agency sees the full picture — HeatGraph does.",
  "cross-silo":
    "When two departments unknowingly affect each other through heat. Example: Buildings approves demolition that makes a nearby school (Education) dangerously hot. Neither sees the link.",
  "risk score":
    "A 0-100 number showing how much temperature affects this entity. Higher = more urgent. Each entity type uses a published research formula specific to its real-world risk (learning loss, ER surge, etc.).",
  "temperature edge":
    "The shared FortyGuard temperature reading that links two nearby entities. If a construction site and a school occupy the same thermal zone, temperature is the invisible thread between them.",
  "factor graph":
    "The mathematical structure behind HeatGraph. Each entity is a node; temperature readings are the edges connecting nearby nodes. Changing one node's temperature ripples through its neighbors.",
  "learning loss":
    "The fraction of a school year's learning students lose due to classroom heat. Each 1°F above 72°F costs about 1% of daily learning (Park et al., NBER 2020). AC prevents 78% of that loss.",
  "heat vulnerability index":
    "NYC DOHMH's official ranking of every neighborhood from 1 (low risk) to 5 (extreme risk), combining surface temperature, poverty, AC access, impervious cover, and green space.",
  "hvi":
    "Heat Vulnerability Index — NYC DOHMH's neighborhood heat-risk ranking (1 = low, 5 = extreme). Computed from temp, poverty, AC access, and greenery.",
  "canopy cover":
    "The percentage of ground area shaded by tree crowns. More canopy = cooler streets. NYC's hottest neighborhoods typically have the least canopy — often due to historical disinvestment.",
  "thermal mass":
    "How much heat a building stores and releases slowly. Brick/concrete NYCHA towers absorb heat all day and radiate it indoors at night, keeping apartments hot hours after sunset.",
  "compound score":
    "A combined 0-100 severity rating for a cross-silo risk pair: receptor vulnerability × emitter proximity amplification. Scores above 70 are critical.",
  "wbgt":
    "Wet-Bulb Globe Temperature — the metric OSHA uses for outdoor worker safety. Combines heat, humidity, and radiant sun load into one number.",
  "what if":
    "A simulation showing how risk scores change if you take an action — delay a project, plant trees, add AC. Use it to compare intervention options before committing resources.",
  "er surge":
    "A spike in emergency room visits triggered by extreme heat. Research shows each 1°C above 29°C (84°F) increases NYC heat-related ER visits by ~3% (Lin et al., DOHMH 2012).",
  "network view":
    "An interactive graph showing compound risks as a web: entity nodes connected by thermal edges. Hover a node to see only its direct connections (Obsidian-style). Filtered by your role.",
  "department":
    "The NYC city agency responsible for this entity — DOB (buildings), DOE (schools), MTA (transit), DHS (shelters), NYCHA (housing), DOHMH (health), DPR (parks), etc.",
};

interface TipProps {
  term: string;
  children: React.ReactNode;
  /** Extra inline styles for the underlined span */
  style?: React.CSSProperties;
}

export function Tip({ term, children, style }: TipProps) {
  const [show, setShow] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  const explanation = GLOSSARY[term.toLowerCase()];
  if (!explanation) return <>{children}</>;

  return (
    <span
      ref={wrapRef}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      style={{
        borderBottom: "1px dotted rgba(148,163,184,0.5)",
        cursor: "help",
        position: "relative",
        display: "inline",
        ...style,
      }}
    >
      {children}
      {show && (
        <span
          style={{
            position: "absolute",
            bottom: "calc(100% + 8px)",
            left: "50%",
            transform: "translateX(-50%)",
            background: "#1e293b",
            border: "1px solid #334155",
            borderRadius: 8,
            padding: "10px 14px",
            width: 280,
            fontSize: 11,
            lineHeight: 1.55,
            color: "#e2e8f0",
            boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
            zIndex: 9999,
            pointerEvents: "none",
            whiteSpace: "normal",
            display: "block",
          }}
        >
          <span style={{ color: "#60a5fa", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
            {term}
          </span>
          <span style={{ display: "block", marginTop: 4 }}>{explanation}</span>
        </span>
      )}
    </span>
  );
}
