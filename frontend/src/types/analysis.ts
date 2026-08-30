/** Types for the /api/analysis endpoint response. */

export interface EntityScore {
  entity_id:    string;
  entity_name:  string;
  entity_type:  string;
  role:         string;   // emitter | receptor | both | sink
  risk_score:   number;   // 0-100
  temperature_c: number;
  temperature_f: number;
  metric_name:  string;
  metric_value: number;
  explanation:  string;
  data_source:  string;
  department:   string;
  lat:          number;
  lon:          number;
  // Enhanced FortyGuard environmental parameters
  wbgt_f?:           number;
  heat_index_f?:     number;
  humidity?:         number;
  aqi?:              number;
  pm25?:             number;
  solar_irradiance?: number;
  no2?:              number;
  env_params_source?: string;
  env_source?:        string;  // alias for env_params_source — "fortyguard_api_cached" | "estimated"
}

export interface CompoundRisk {
  emitter_id:          string;
  emitter_name:        string;
  emitter_type:        string;
  emitter_department:  string;
  emitter_delta_t:     number;
  emitter_lat:         number;
  emitter_lon:         number;
  receptor_id:         string;
  receptor_name:       string;
  receptor_type:       string;
  receptor_department: string;
  receptor_risk_score: number;
  receptor_lat:        number;
  receptor_lon:        number;
  distance_m:          number;
  compound_score:      number;
  insight:             string;
  departments:         string[];
  mid_lat:             number;
  mid_lon:             number;
}

export interface AnalysisSummary {
  city:               string;
  total_entities:     number;
  scored_entities:    number;
  skipped_no_tile:    number;
  skipped_no_factor:  number;
  high_risk_count:    number;
  medium_risk_count:  number;
  compound_risk_count: number;
  departments_in_compounds: string[];
  tile_count:         number;
}

export interface AnalysisResponse {
  summary:        AnalysisSummary;
  entity_scores:  EntityScore[];
  compound_risks: CompoundRisk[];
}
