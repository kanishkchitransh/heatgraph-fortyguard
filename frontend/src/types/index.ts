import type { FeatureCollection } from "geojson";

export type EntityType =
  | "school"
  | "hospital"
  | "shelter"
  | "tree_canopy"
  | "cooling_center"
  | "fire_station"
  | "capital_project"
  | "construction_permit"
  | "nycha_development"
  | "subway_station"
  | "hvi_zone"
  | "census_tract"
  | "tree_aggregate";

export type EntityRole = "emitter" | "receptor" | "both" | "sink";

export interface CityEntity {
  id:          string;
  name:        string;
  entity_type: EntityType;
  role:        EntityRole;
  lat:         number;
  lon:         number;
  city:        string;
  address:     string;
  extra:       Record<string, unknown>;
}

export interface EntitiesResponse {
  total:    number;
  offset:   number;
  limit:    number;
  entities: CityEntity[];
}

/** Role selector — each role corresponds to a government department's view. */
export const ROLES = {
  planner: {
    label:        "🏛️ City Planner",
    color:        "#f59e0b",
    entity_types: null as EntityType[] | null,
    role_filter:  null as EntityRole | null,
    api_role:     "planner",
  },
  health: {
    label:        "🏥 Health Officer",
    color:        "#ef4444",
    entity_types: ["hospital", "shelter", "nycha_development", "hvi_zone", "cooling_center"] as EntityType[],
    role_filter:  "receptor" as EntityRole,
    api_role:     "health",
  },
  schools: {
    label:        "🎓 School District",
    color:        "#8b5cf6",
    entity_types: ["school", "construction_permit", "capital_project"] as EntityType[],
    role_filter:  null as EntityRole | null,
    api_role:     "schools",
  },
  infra: {
    label:        "⚡ Infrastructure",
    color:        "#06b6d4",
    entity_types: ["subway_station", "construction_permit", "capital_project"] as EntityType[],
    role_filter:  null as EntityRole | null,
    api_role:     "infrastructure",
  },
  community: {
    label:        "🏘️ Community",
    color:        "#22c55e",
    entity_types: ["school", "shelter", "tree_canopy", "cooling_center", "hvi_zone", "nycha_development"] as EntityType[],
    role_filter:  null as EntityRole | null,
    api_role:     "community",
  },
} as const;

export type RoleKey = keyof typeof ROLES;

/** NYC — the only city. */
export const NYC = {
  label:  "New York City",
  center: [40.7128, -74.0060] as [number, number],
  bbox:   [-74.05, 40.68, -73.90, 40.82] as [number, number, number, number],
  zoom:   12,
} as const;

/** Matches FortyGuard's real result shape. */
export interface HeatmapResponse {
  _cached?: boolean;
  _mock?:   boolean;
  map_data?:  FeatureCollection;
  stats_data?: {
    temperature_stats?: {
      Minimum: number; Maximum: number; Mean: number; Standard_deviation: number;
    };
    Temperature_stats?: {
      Minimum: number; Maximum: number; Mean: number; Standard_deviation: number;
    };
    units?: string;
  };
  metadata?: Record<string, unknown>;
}
