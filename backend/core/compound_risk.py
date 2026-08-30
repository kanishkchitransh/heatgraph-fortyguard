"""
Compound Risk Detection — the cross-silo product insight.

Finds (emitter, receptor) pairs from DIFFERENT departments that share the
same thermal zone (within `max_distance_m` of each other). These are risks
that no single department can see because they each only own one half.

Example:
  DOB construction permit (emitter, DOB) 280m from P.S. 46 with no AC
  (receptor, DOE) — neither agency sees the thermal connection, but the
  construction site's +2.5°F pushes the school further past the safe threshold.

The compound_score combines:
  - receptor vulnerability (risk_score)
  - emitter perturbation magnitude (metric_value = delta_t_f)
  - proximity factor (closer → worse)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.factors import FactorResult


@dataclass
class CompoundRisk:
    emitter:        FactorResult
    receptor:       FactorResult
    distance_m:     float
    compound_score: float       # 0-100
    insight:        str         # plain-English explanation for the insight card
    departments:    list[str]   # the two agencies that each own only one side


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two lat/lon points (Haversine)."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _short_agency(department: str) -> str:
    """Extract the short acronym from 'NYC DOB (Dept of Buildings)' → 'DOB'."""
    # department looks like: "NYC DOE (Dept of Education)"
    parts = department.split("(")
    label = parts[0].strip()          # "NYC DOE"
    words = label.split()
    # Last word is typically the acronym
    return words[-1] if words else department


def find_compound_risks(
    factor_results: list[FactorResult],
    max_distance_m: float = 500.0,
    min_receptor_score: float = 30.0,
) -> list[CompoundRisk]:
    """
    Find all (emitter, receptor) pairs that:
      1. Are within max_distance_m of each other
      2. Belong to different departments
      3. Have receptor risk_score >= min_receptor_score

    Returns list sorted by compound_score descending (most severe first).
    Capped at 200 results to keep API responses manageable.
    """
    emitters  = [r for r in factor_results if r.role in ("emitter", "both")]
    receptors = [r for r in factor_results if r.role in ("receptor", "both") and r.risk_score >= min_receptor_score]

    compound_risks: list[CompoundRisk] = []

    for emitter in emitters:
        for receptor in receptors:
            # Skip self-pairing (a "both" entity pairing with itself)
            if emitter.entity_id == receptor.entity_id:
                continue

            # Skip same-department pairs — intra-agency risk, not cross-silo
            if emitter.department == receptor.department:
                continue

            dist = haversine_m(emitter.lat, emitter.lon, receptor.lat, receptor.lon)
            if dist > max_distance_m:
                continue

            # Compound score: receptor vulnerability amplified by emitter proximity
            # proximity_factor: 1.0 at distance 0, 0.0 at max_distance_m
            proximity = 1.0 - (dist / max_distance_m)
            # emitter.metric_value is delta_t_f for construction, grate_delta for subway, etc.
            amplification = 1.0 + proximity * min(emitter.metric_value / 5.0, 1.0)
            compound_score = min(receptor.risk_score * amplification, 100.0)

            agt_e = _short_agency(emitter.department)
            agt_r = _short_agency(receptor.department)

            insight = (
                f"🔗 CROSS-SILO RISK: {emitter.entity_name} "
                f"({agt_e}) is {dist:.0f} m from "
                f"{receptor.entity_name} ({agt_r}). "
                f"The {emitter.entity_type.replace('_', ' ')} adds an estimated "
                f"+{emitter.metric_value:.1f}°F to the block. "
                f"The {receptor.entity_type.replace('_', ' ')}'s risk score is "
                f"{receptor.risk_score:.0f}/100. "
                f"Neither {agt_e} nor {agt_r} currently sees this thermal connection."
            )

            compound_risks.append(CompoundRisk(
                emitter=emitter,
                receptor=receptor,
                distance_m=round(dist, 1),
                compound_score=round(compound_score, 1),
                insight=insight,
                departments=[emitter.department, receptor.department],
            ))

    compound_risks.sort(key=lambda x: x.compound_score, reverse=True)

    # Ensure cross-department diversity: keep top 50 from each unique dept pair,
    # then interleave so no single department pair monopolises the list.
    seen_pairs: dict[str, list[CompoundRisk]] = {}
    for c in compound_risks:
        key = "-".join(sorted([c.emitter.department, c.receptor.department]))
        seen_pairs.setdefault(key, [])
        if len(seen_pairs[key]) < 50:
            seen_pairs[key].append(c)

    # Round-robin merge across department pairs (diversity-first)
    merged: list[CompoundRisk] = []
    buckets = list(seen_pairs.values())
    max_len = max(len(b) for b in buckets) if buckets else 0
    for i in range(max_len):
        for bucket in buckets:
            if i < len(bucket):
                merged.append(bucket[i])

    return merged[:200]
