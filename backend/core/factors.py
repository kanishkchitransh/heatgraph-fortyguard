"""
Factor functions for the Forney-style factor graph.

Each function takes a FullEnvParams (temperature + WBGT + AQI + PM2.5 +
solar irradiance + humidity) and returns a FactorResult with:
  - risk_score (0-100): how much this entity is at risk / how much it perturbs
  - explanation: plain-English, readable by a non-technical person
  - data_source: real academic/agency citation

Factor graph structure
----------------------
  EDGES (variables) : FortyGuard temperature at each grid cell (+ derived env params)
  NODES (factors)   : These functions — they consume the edge variable
                      and produce risk scores

Roles
-----
  emitter  — raises local temperature (construction sites, impervious cover)
  receptor — harmed by temperature (schools, NYCHA residents)
  both     — both emitter and receptor (subway stations: hot platform + grate exhaust)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from core.tile_lookup import FullEnvParams


@dataclass
class FactorResult:
    entity_id:    str
    entity_name:  str
    entity_type:  str
    role:         str       # emitter | receptor | both | sink
    risk_score:   float     # 0-100
    temperature_c: float
    temperature_f: float
    metric_name:  str       # what metric_value measures
    metric_value: float
    explanation:  str       # plain English
    data_source:  str       # citation
    department:   str       # which city agency owns this entity
    lat:          float
    lon:          float
    # Enhanced FortyGuard environmental parameters (from FullEnvParams)
    wbgt_f: float = 0.0
    heat_index_f: float = 0.0
    humidity: float = 0.0
    aqi: float = 0.0
    pm25: float = 0.0
    solar_irradiance: float = 0.0
    no2: float = 0.0
    env_params_source: str = "estimated"


def _env_fields(env: FullEnvParams) -> dict:
    """Return the optional env fields to populate in every FactorResult."""
    return {
        "wbgt_f": env.wbgt_f,
        "heat_index_f": env.heat_index_f,
        "humidity": env.humidity,
        "aqi": env.aqi,
        "pm25": env.pm25,
        "solar_irradiance": env.solar_irradiance,
        "no2": env.no2,
        "env_params_source": env.env_params_source,
    }


# ---------------------------------------------------------------------------
# R1 — School receptor
# ---------------------------------------------------------------------------
def compute_school_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    R1: School receptor — learning loss + PM2.5 respiratory absence risk.

    Heat dose-response: each 1°F above 72°F indoor threshold reduces learning
    by ~1% per day. AC offsets ~78% of the effect.
    PM2.5: each 10 μg/m³ above 12 → ~1.5% additional school absences.
    Source: Park, Goodman, Hurwitz & Smith (NBER/AEJ 2020); EPA PM2.5 health effects.
    """
    attrs      = entity.attributes
    has_ac     = bool(attrs.get("has_ac", True))
    enrollment = int(attrs.get("enrollment", 400))

    temp_f = env.temperature_f
    threshold_f = 72.0

    if temp_f <= threshold_f:
        learning_loss_pct = 0.0
    else:
        excess = temp_f - threshold_f
        learning_loss_pct = excess * 0.01 * (0.22 if has_ac else 1.0)

    # PM2.5 respiratory absence factor
    pm25_absence_pct = max(0.0, (env.pm25 - 12.0) / 10.0 * 0.015) if env.pm25 > 0 else 0.0

    total_risk = min(learning_loss_pct + pm25_absence_pct, 1.0)
    risk_score = min(total_risk * 350, 100)

    students_affected = int(enrollment * learning_loss_pct) if not has_ac else 0
    ac_status = "has AC" if has_ac else "NO AC"
    explanation = (
        f"{entity.name} ({ac_status}): at {temp_f:.1f}°F (HI: {env.heat_index_f:.1f}°F), "
        f"estimated {learning_loss_pct:.1%} learning loss per school day. "
        f"PM2.5: {env.pm25:.1f} μg/m³ → +{pm25_absence_pct:.1%} absence risk. "
        f"~{students_affected} students affected."
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="school",
        role="receptor",
        risk_score=round(risk_score, 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="learning_loss_pct",
        metric_value=round(learning_loss_pct, 4),
        explanation=explanation,
        data_source="Park, Goodman, Hurwitz & Smith (NBER/AEJ 2020); EPA PM2.5 health effects; NYC DOE school data",
        department="NYC DOE (Dept of Education)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# R2 — NYCHA receptor
# ---------------------------------------------------------------------------
def compute_nycha_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    R2: NYCHA public housing receptor — indoor heat mortality risk.

    Masonry construction raises indoor temps 2-5°C above ambient.
    High humidity prevents evaporative cooling, amplifying indoor apparent temp.
    58% of NYC heat deaths occur in homes without AC.
    Source: NYC DOHMH Heat Mortality Reports; NYCHA development data.
    """
    attrs           = entity.attributes
    ac_penetration  = float(attrs.get("ac_penetration", 0.42))
    thermal_mass    = attrs.get("thermal_mass", "high")
    has_central_ac  = bool(attrs.get("has_central_ac", False))
    total_units     = int(attrs.get("total_units", 500))

    temp_f = env.temperature_f

    # Masonry thermal mass: indoor temp exceeds outdoor by 2-5°C
    indoor_excess_c = 3.5 if thermal_mass == "high" else 1.5
    indoor_temp_c   = env.temperature_c + indoor_excess_c
    indoor_temp_f   = indoor_temp_c * 9 / 5 + 32

    # Humidity trapping: masonry buildings don't ventilate well
    humidity_penalty_f = max(0.0, (env.humidity - 60) * 0.15)
    apparent_indoor_f  = indoor_temp_f + humidity_penalty_f

    if apparent_indoor_f <= 82:
        base_risk = 0.0
    elif apparent_indoor_f <= 90:
        base_risk = (apparent_indoor_f - 82) * 5        # 0 → 40
    elif apparent_indoor_f <= 100:
        base_risk = 40 + (apparent_indoor_f - 90) * 4  # 40 → 80
    else:
        base_risk = 80 + (apparent_indoor_f - 100) * 2

    ac_reduction = ac_penetration * (1.0 if has_central_ac else 0.7)
    risk_score   = min(base_risk * (1 - ac_reduction), 100)

    units_at_risk = int(total_units * (1 - ac_penetration))
    explanation = (
        f"{entity.name}: outdoor {env.temperature_f:.1f}°F ({env.humidity:.0f}% humidity). "
        f"Estimated indoor apparent temp {apparent_indoor_f:.1f}°F "
        f"(masonry +{indoor_excess_c:.1f}°C + humidity trapping). "
        f"AC penetration {ac_penetration:.0%}. "
        f"~{units_at_risk} units without AC."
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="nycha_development",
        role="receptor",
        risk_score=round(risk_score, 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="indoor_apparent_f",
        metric_value=round(apparent_indoor_f, 1),
        explanation=explanation,
        data_source="NYC DOHMH Heat Mortality Reports; NYCHA Development Data",
        department="NYCHA (Housing Authority)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# R3/E3 — Subway station (both emitter and receptor)
# ---------------------------------------------------------------------------
def compute_subway_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    R3/E3: Subway — BOTH receptor (platform heat WBGT) and emitter (grate exhaust).

    Underground: platform WBGT = surface WBGT + delta (humidity-adjusted).
    OSHA WBGT thresholds used for passenger/worker safety classification.
    Elevated: steel tracks at risk above 95°F; riders exposed to direct sun.
    Source: MTA platform temperature studies; NYC DOT grate measurements; OSHA Heat NPRM.
    """
    attrs          = entity.attributes
    is_underground = bool(attrs.get("is_underground", True))
    platform_delta = float(attrs.get("platform_temp_delta_c", 8.0))
    grate_delta    = float(attrs.get("grate_temp_delta_c", 3.0))
    routes         = attrs.get("routes", "?")

    temp_f = env.temperature_f

    if is_underground:
        platform_c = env.temperature_c + platform_delta
        platform_f = platform_c * 9 / 5 + 32

        # Platform WBGT: enclosed humidity amplifies effect
        platform_wbgt_f = env.wbgt_f + (platform_delta * 9 / 5 * 0.8)

        # OSHA WBGT thresholds
        if platform_wbgt_f < 82:
            safety_level = "acceptable"
            risk_score = max(10.0, (platform_wbgt_f - 72) * 3)
        elif platform_wbgt_f < 87:
            safety_level = "caution"
            risk_score = 40 + (platform_wbgt_f - 82) * 8
        elif platform_wbgt_f < 90:
            safety_level = "warning"
            risk_score = 80 + (platform_wbgt_f - 87) * 4
        else:
            safety_level = "danger"
            risk_score = 95.0

        explanation = (
            f"{entity.name} (underground · {routes}): "
            f"street WBGT {env.wbgt_f:.1f}°F → platform WBGT {platform_wbgt_f:.1f}°F "
            f"({platform_delta:.0f}°C above street). "
            f"Passenger heat safety: {safety_level}. "
            f"Sidewalk grates emit +{grate_delta:.1f}°C into pedestrian zones."
        )
        metric_name  = "platform_wbgt_f"
        metric_value = round(platform_wbgt_f, 1)
    else:
        track_f = temp_f + 30  # steel in direct sun
        if temp_f > 95:
            risk_score = 70 + min((temp_f - 95) * 3, 30)
        elif temp_f > 90:
            risk_score = 40 + (temp_f - 90) * 6
        else:
            risk_score = max(0.0, (temp_f - 80) * 4)

        speed_note = "Speed restrictions likely." if temp_f > 90 else "Normal operations."
        explanation = (
            f"{entity.name} (elevated · {routes}): "
            f"ambient {temp_f:.1f}°F, steel rail ~{track_f:.0f}°F. {speed_note}"
        )
        metric_name  = "track_temp_f"
        metric_value = round(track_f, 1)

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="subway_station",
        role="both",
        risk_score=round(min(risk_score, 100), 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name=metric_name,
        metric_value=metric_value,
        explanation=explanation,
        data_source="MTA platform temperature data; NYC DOT grate studies; OSHA Heat NPRM 89 FR 70698",
        department="MTA (Metropolitan Transportation Authority)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# E1 — Construction permit emitter
# ---------------------------------------------------------------------------
def compute_construction_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    E1: Construction permit — EMITTER + worker safety via WBGT.

    Active construction removes canopy, generates PM2.5 dust, exposes impervious
    surface, and runs heavy diesel equipment.
    Solar irradiance amplifies heat from fresh asphalt (absorbs 95% vs 80% for vegetation).
    Source: Akbari et al. urban canopy literature; NYC DOB permit data; OSHA NPRM.
    """
    attrs    = entity.attributes
    job_type  = attrs.get("job_type", "A1")
    work_type = attrs.get("work_type", "Major Alteration")

    temp_f = env.temperature_f

    work_upper = work_type.upper()
    is_demo = ("DEMOLITION" in work_upper) or (job_type in ("DM",))
    is_new  = ("NEW BUILDING" in work_upper) or (job_type in ("NB",))

    if is_new:
        base_delta_f = 2.5
        canopy_note  = "full site clearing, canopy removal"
    elif is_demo:
        base_delta_f = 1.8
        canopy_note  = "debris, dust, exposed impervious surface"
    else:
        base_delta_f = 1.0
        canopy_note  = "partial site disruption"

    # Solar irradiance amplifies construction heat: fresh asphalt absorbs 95% vs 80% for vegetation
    # Each 100 W/m² above 400 W/m² adds ~15% to the thermal delta
    solar_amplifier = 1.0 + max(0.0, (env.solar_irradiance - 400) / 750.0) * 0.3
    delta_t_f = round(base_delta_f * solar_amplifier, 1)

    # Worker WBGT safety (dual role: emitter + receptor)
    wbgt_f = env.wbgt_f
    if wbgt_f >= 90:
        worker_risk = "STOP WORK — WBGT exceeds OSHA danger threshold"
        rest_note   = "60 min rest/hr"
    elif wbgt_f >= 87:
        worker_risk = "HIGH — 45 min rest/hr required (OSHA)"
        rest_note   = "45/15 work/rest"
    elif wbgt_f >= 82:
        worker_risk = "MODERATE — 30 min rest/hr (OSHA)"
        rest_note   = "30/30 work/rest"
    else:
        worker_risk = "NORMAL"
        rest_note   = "standard schedule"

    if temp_f > 90:
        risk_score = min(delta_t_f * 25 + (temp_f - 90) * 2, 100)
    else:
        risk_score = min(delta_t_f * 20, 100)

    explanation = (
        f"{entity.name}: {job_type} — {work_type}. "
        f"Solar load {env.solar_irradiance:.0f} W/m² → estimated +{delta_t_f:.1f}°F "
        f"local thermal perturbation ({canopy_note}). "
        f"Worker safety (WBGT {wbgt_f:.1f}°F): {worker_risk} ({rest_note})."
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="construction_permit",
        role="emitter",
        risk_score=round(risk_score, 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="delta_t_f",
        metric_value=delta_t_f,
        explanation=explanation,
        data_source="Akbari et al. urban canopy literature; NYC DOB permit data; OSHA Heat NPRM 89 FR 70698",
        department="NYC DOB (Dept of Buildings)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# R4 — Heat Vulnerability Index zone
# ---------------------------------------------------------------------------
def compute_hvi_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    R4: HVI zone receptor — compound socio-environmental vulnerability.

    HVI rank 1-5 (5 = most vulnerable). Combines surface temp, AC access,
    green space, poverty, and demographic vulnerability.
    Source: NYC DOHMH Heat Vulnerability Index.
    """
    attrs    = entity.attributes
    hvi_rank = int(attrs.get("hvi_rank", 3))
    zip_code = attrs.get("zip_code", "?")

    temp_f = env.temperature_f

    base = max(0.0, (temp_f - 80) * 2)
    multiplier = hvi_rank / 3.0
    risk_score = min(base * multiplier, 100)

    labels = {1: "Low", 2: "Low-Moderate", 3: "Moderate", 4: "High", 5: "Very High"}
    priority = " Priority intervention zone." if hvi_rank >= 4 else ""
    explanation = (
        f"ZIP {zip_code} (HVI rank {hvi_rank}/5 — {labels.get(hvi_rank, '?')}): "
        f"at {temp_f:.1f}°F ambient (WBGT {env.wbgt_f:.1f}°F), "
        f"compound vulnerability score {risk_score:.0f}/100.{priority}"
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="hvi_zone",
        role="receptor",
        risk_score=round(risk_score, 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="compound_vulnerability",
        metric_value=round(risk_score, 1),
        explanation=explanation,
        data_source="NYC DOHMH Heat Vulnerability Index",
        department="NYC DOHMH (Dept of Health)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# E2 — Capital Project emitter (CPDB)
# ---------------------------------------------------------------------------
def compute_capital_project_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    E2: CPDB capital project — EMITTER.

    Long-duration city capital projects (DDC, DEP, DOT…) disrupt canopy,
    expose impervious surface, and run heavy machinery for months to years.
    Source: NYC Capital Projects Database (CPDB FY26 Exec); Akbari et al.
    """
    attrs      = entity.attributes
    typecat    = attrs.get("typecat", "Fixed Asset")
    delta_t_f  = float(attrs.get("delta_t_f", 1.5))
    agency     = attrs.get("agency", "DDC")
    descript   = attrs.get("description", entity.name)
    pctotal    = float(attrs.get("pctotal", 0))
    maxdate    = attrs.get("maxdate", "")
    department = attrs.get("department", f"NYC {agency} (Capital Projects)")

    temp_f = env.temperature_f

    if temp_f > 90:
        risk_score = min(delta_t_f * 20 + (temp_f - 90) * 2, 100)
    else:
        risk_score = min(delta_t_f * 16, 100)

    budget_str = f"${pctotal / 1e6:.1f}M" if pctotal >= 1e6 else f"${pctotal:,.0f}"
    completion = f", est. completion {maxdate[:7]}" if maxdate else ""
    explanation = (
        f"{descript[:80]} [{agency}]: {typecat} capital project "
        f"({budget_str} planned{completion}). "
        f"Estimated local temp increase: +{delta_t_f:.1f}°F. "
        f"Solar load {env.solar_irradiance:.0f} W/m² amplifies impervious-surface heat gain."
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="capital_project",
        role="emitter",
        risk_score=round(risk_score, 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="delta_t_f",
        metric_value=round(delta_t_f, 2),
        explanation=explanation,
        data_source="NYC CPDB FY26 Exec; Akbari et al. urban canopy literature",
        department=department,
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# R5 — Hospital / healthcare facility receptor
# ---------------------------------------------------------------------------
def compute_hospital_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    R5: Hospital / healthcare facility — RECEPTOR.

    ER visits rise 2.7–3.1% per 1°C above 29°C threshold (Lin et al., NYC SPARCS).
    AQI compound effect: each 10-point AQI above 50 → +2% respiratory admissions.
    PM2.5 direct effect strongest for nursing homes and psychiatric facilities.
    Source: Lin et al. 2012 (NYC SPARCS); AHA 2024; EPA AQI health effects.
    """
    attrs    = entity.attributes
    subtype  = attrs.get("subtype", "health_clinic")
    capacity = attrs.get("capacity") or 200

    temp_c     = env.temperature_c
    temp_f     = env.temperature_f
    threshold_c = 29.0

    excess_c  = max(0.0, temp_c - threshold_c)
    surge_pct = excess_c * 3.0

    # AQI compound effect on respiratory admissions
    aqi_surge = max(0.0, (env.aqi - 50) / 10 * 2.0)

    # PM2.5 direct effect (strongest for nursing homes / psychiatric facilities)
    pm25_surge = max(0.0, (env.pm25 - 12.0) * 0.5)

    multiplier = {
        "hospital":             1.0,
        "nursing_home":         1.8,
        "psychiatric_facility": 1.5,
        "senior_center":        1.3,
        "substance_treatment":  1.2,
        "health_clinic":        1.0,
    }.get(subtype, 1.0)

    total_surge = (surge_pct + aqi_surge + pm25_surge) * multiplier
    risk_score  = min(total_surge * 2.5, 100)

    notes = {
        "nursing_home":         "Bed-bound residents and polypharmacy severely limit heat response.",
        "psychiatric_facility": "Antipsychotics (e.g. clozapine) impair sweating and thermoregulation.",
        "senior_center":        "Elderly visitors may have no AC at home — this centre is their refuge.",
    }.get(subtype, "")

    explanation = (
        f"{entity.name} ({subtype}): {temp_f:.1f}°F → +{surge_pct:.1f}% heat ER surge. "
        f"AQI {env.aqi:.0f} → +{aqi_surge:.1f}% respiratory admissions. "
        f"PM2.5 {env.pm25:.1f} μg/m³ → +{pm25_surge:.1f}% pulmonary risk. "
        f"Combined ({multiplier:.1f}× multiplier): {risk_score:.0f}/100. {notes}"
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="hospital",
        role="receptor",
        risk_score=round(risk_score, 1),
        temperature_c=round(temp_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="er_surge_pct",
        metric_value=round(total_surge, 2),
        explanation=explanation,
        data_source="Lin et al. 2012 (NYC SPARCS); AHA 2024 cardiovascular heat statement; EPA AQI health effects",
        department="NYC Health+Hospitals / DOHMH",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# R6 — Homeless shelter receptor
# ---------------------------------------------------------------------------
def compute_shelter_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    R6: Homeless shelter — RECEPTOR (NYC DHS).

    NYC shelters house ~90,000 people nightly. Many buildings lack adequate
    cooling. Residents have limited self-protection capacity. 45% of
    Maricopa County 2023 heat deaths were people experiencing homelessness.
    Indoor temp accumulates like NYCHA masonry + overcrowding (+4°C).
    Source: NYC DHS facility data; Maricopa County 2023 heat mortality report.
    """
    attrs    = entity.attributes
    has_ac   = bool(attrs.get("has_ac", False))
    capacity = attrs.get("capacity") or 80

    temp_f = env.temperature_f

    indoor_delta_c = 0.0 if has_ac else 4.0
    indoor_f = (env.temperature_c + indoor_delta_c) * 9 / 5 + 32

    if indoor_f <= 82:
        risk_score = 15
    elif indoor_f <= 90:
        risk_score = 30 + (indoor_f - 82) * 5.0
    elif indoor_f <= 100:
        risk_score = 70 + (indoor_f - 90) * 3.0
    else:
        risk_score = 100

    risk_score = min(risk_score, 100)

    ac_note = "has AC" if has_ac else "no AC — heat accumulates indoors"
    explanation = (
        f"{entity.name} (capacity ~{capacity}): {ac_note}. "
        f"Estimated indoor temp {indoor_f:.1f}°F at ambient {temp_f:.1f}°F "
        f"(WBGT {env.wbgt_f:.1f}°F). "
        f"Risk {risk_score:.0f}/100. Residents have limited self-protection capacity."
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="shelter",
        role="receptor",
        risk_score=round(risk_score, 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="indoor_temp_f",
        metric_value=round(indoor_f, 1),
        explanation=explanation,
        data_source="NYC DHS; Maricopa County 2023 heat mortality report (45% unhoused)",
        department="NYC DHS (Dept of Homeless Services)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# S1 — Tree canopy SINK (negative emitter)
# ---------------------------------------------------------------------------
def compute_tree_canopy_effect(entity, env: FullEnvParams) -> FactorResult:
    """
    S1: Tree canopy block — SINK (NYC Parks & Recreation).

    Street trees cool ambient temperature by 0.2–2.0°C depending on density.
    Each 10% increase in canopy cover → −0.5 to −1.3°C local cooling.
    Source: Akbari et al. 2001; NYC CAPA urban heat study.
    """
    attrs     = entity.attributes
    n         = attrs.get("tree_count", 0)
    avg_dbh   = attrs.get("avg_dbh_inches", 10)
    density   = attrs.get("canopy_density", "minimal")
    cooling_c = attrs.get("cooling_c", 0.2)

    temp_f    = env.temperature_f
    cooling_f = cooling_c * 9 / 5

    benefit = {"dense": 85, "moderate": 55, "sparse": 25, "minimal": 8}.get(density, 8)

    # Higher solar irradiance means more cooling benefit from canopy shade
    solar_bonus = min(int((env.solar_irradiance - 400) / 100 * 2), 10) if env.solar_irradiance > 400 else 0
    benefit = min(benefit + solar_bonus, 100)

    planting_note = " Priority area for tree planting." if density in ("minimal", "sparse") else ""
    explanation = (
        f"Tree canopy block: {n} living trees, avg trunk {avg_dbh:.0f}\" DBH ({density}). "
        f"Estimated cooling: −{cooling_f:.1f}°F from ambient {temp_f:.1f}°F. "
        f"Solar load {env.solar_irradiance:.0f} W/m² → canopy shade benefit {benefit}/100.{planting_note}"
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="tree_canopy",
        role="sink",
        risk_score=float(benefit),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="cooling_delta_f",
        metric_value=round(-cooling_f, 2),
        explanation=explanation,
        data_source="NYC Parks 2015 Street Tree Census; Akbari et al. urban canopy cooling literature",
        department="NYC Parks & Recreation (DPR)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# S2 — Cooling / evacuation centre SINK
# ---------------------------------------------------------------------------
def compute_cooling_center_effect(entity, env: FullEnvParams) -> FactorResult:
    """
    S2: NYC official cooling / evacuation centre — SINK.

    When activated during heat emergencies, these centres provide guaranteed
    air-conditioned refuge. Value increases non-linearly above 90°F.
    Source: NYC Emergency Management; CDC heat emergency guidelines.
    """
    attrs      = entity.attributes
    accessible = bool(attrs.get("accessible", True))

    temp_f = env.temperature_f

    if temp_f < 85:
        benefit = 20
    elif temp_f < 90:
        benefit = 40 + (temp_f - 85) * 4
    elif temp_f < 95:
        benefit = 60 + (temp_f - 90) * 6
    else:
        benefit = min(90 + (temp_f - 95) * 2, 100)

    accessibility_note = " ADA accessible." if accessible else ""
    explanation = (
        f"{entity.name}: designated NYC cooling/evacuation centre. "
        f"At ambient {temp_f:.1f}°F (WBGT {env.wbgt_f:.1f}°F), benefit score {benefit:.0f}/100. "
        f"Provides guaranteed AC refuge during heat emergencies.{accessibility_note}"
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=entity.name,
        entity_type="cooling_center",
        role="sink",
        risk_score=float(round(benefit, 1)),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(temp_f, 2),
        metric_name="cooling_benefit",
        metric_value=round(benefit, 1),
        explanation=explanation,
        data_source="NYC Emergency Management; NYC OEM evacuation centre data",
        department="NYC OEM (Office of Emergency Management)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# NEW: Transit Worker Safety receptor (Smart Mobility use case)
# ---------------------------------------------------------------------------
def compute_transit_worker_risk(entity, env: FullEnvParams) -> FactorResult:
    """
    Transit worker safety — receptor using WBGT (Smart Mobility use case).

    Implements FortyGuard's Smart Mobility use case: "integrate thermal
    comfort-based routing and forecasted heat zones into transportation systems
    to optimize routes and improve worker safety."

    OSHA NPRM heat thresholds (89 FR 70698, Aug 2024) applied to MTA workers.
    Source: OSHA Heat NPRM; MTA platform temperature studies.
    """
    attrs          = entity.attributes
    is_underground = bool(attrs.get("is_underground", True))
    platform_delta = float(attrs.get("platform_temp_delta_c", 8.0))

    if is_underground:
        worker_wbgt_f = env.wbgt_f + (platform_delta * 9 / 5 * 0.7)
        context = "underground platform"
    else:
        # Surface/elevated: direct sun exposure adds solar heating
        worker_wbgt_f = env.wbgt_f + (env.solar_irradiance / 750 * 3.0)
        context = "outdoor/elevated station"

    # OSHA NPRM thresholds for high-exertion outdoor work
    if worker_wbgt_f < 80:
        rest_min, risk_level = 0, "safe"
    elif worker_wbgt_f < 85:
        rest_min, risk_level = 15, "caution"
    elif worker_wbgt_f < 88:
        rest_min, risk_level = 30, "warning"
    elif worker_wbgt_f < 90:
        rest_min, risk_level = 45, "danger"
    else:
        rest_min, risk_level = 60, "EXTREME — stop outdoor work"

    risk_score = min((rest_min / 60) * 100, 100)
    productivity_loss_pct = rest_min / 60 * 100
    routes = attrs.get("routes", "?")

    explanation = (
        f"MTA worker safety at {entity.name} ({context} · {routes}): "
        f"WBGT {worker_wbgt_f:.1f}°F → {risk_level}. "
        f"Required: {rest_min} min rest/hour (OSHA NPRM). "
        f"Productivity reduction: {productivity_loss_pct:.0f}%."
    )

    return FactorResult(
        entity_id=entity.id,
        entity_name=f"[Worker] {entity.name}",
        entity_type="subway_station",
        role="receptor",
        risk_score=round(risk_score, 1),
        temperature_c=round(env.temperature_c, 2),
        temperature_f=round(env.temperature_f, 2),
        metric_name="worker_wbgt_f",
        metric_value=round(worker_wbgt_f, 1),
        explanation=explanation,
        data_source="OSHA Heat NPRM (89 FR 70698, Aug 2024); MTA platform temperature studies",
        department="MTA (Metropolitan Transportation Authority)",
        lat=entity.lat,
        lon=entity.lon,
        **_env_fields(env),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
FACTOR_REGISTRY: dict[str, callable] = {
    # Receptors (harmed by heat)
    "school":               compute_school_risk,
    "nycha_development":    compute_nycha_risk,
    "subway_station":       compute_subway_risk,      # role=both
    "hvi_zone":             compute_hvi_risk,
    "hospital":             compute_hospital_risk,
    "shelter":              compute_shelter_risk,
    # Emitters (raise local temperature)
    "construction_permit":  compute_construction_risk,
    "capital_project":      compute_capital_project_risk,
    # Sinks (cool the thermal field)
    "tree_canopy":          compute_tree_canopy_effect,
    "cooling_center":       compute_cooling_center_effect,
}
