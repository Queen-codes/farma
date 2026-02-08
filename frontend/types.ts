
export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'awaiting_human';

// Matches backend JobEvent from /api/jobs/{id}/events
export interface JobEvent {
  event_id: string;
  job_id: string;
  created_at: string;
  event_type: string;
  status: string;
  step: string | null;
  message: string | null;
  progress: number | null;
  payload: Record<string, any> | null;
}

// Matches backend JobResponse from /api/jobs/{id}
export interface JobResponse {
  job_id: string;
  job_type: string;
  status: JobStatus;
  started_at: string | null;
  completed_at: string | null;
  result: Record<string, any> | null;
}

// Matches backend JobEventsResponse
export interface JobEventsResponse {
  job_id: string;
  events: JobEvent[];
}

// Matches backend StateIntelligenceSummary
export interface StateIntelligenceSummary {
  state_name: string;
  conflict_events: number;
  idp_estimate: number | null;
  idp_trend: string;
  food_insecurity_level: string;
  ipc_phase: number | null;
  markets_operational: string;
  priority_level: string | null;
  priority_score: number | null;
}

// Matches backend AegisScanStatusResponse
export interface AegisScanStatusResponse {
  scan_id: number;
  run_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  states_scanned: number;
  total_events: number;
  total_fatalities: number;
  state_summaries: StateSummaryEntry[] | null;
  conflict_events: ConflictEventSummary[] | null;
  lga_risk: LGARiskEntry[] | null;
}

// Matches backend StateSummaryEntry
export interface StateSummaryEntry {
  state_name: string;
  conflict_events: number;
  idp_estimate: number | null;
  idp_trend: string;
  food_insecurity_level: string;
  ipc_phase: number | null;
  markets_operational: string;
  priority_level: string | null;
  priority_score: number | null;
}

// Matches backend ConflictEventSummary
export interface ConflictEventSummary {
  state: string;
  lga: string | null;
  event_type: string;
  fatalities: number | null;
  date: string | null;
  summary: string | null;
  location: string | null;
  lat: number | null;
  lon: number | null;
}

// Matches backend LGARiskEntry
export interface LGARiskEntry {
  lga: string;
  state: string;
  event_count: number;
  fatalities: number;
  risk_score: number;
  risk_level: string;
}

// Matches backend AegisDashboardResponse
export interface AegisDashboardResponse {
  latest_scan: AegisScanStatusResponse | null;
  total_scans: number;
  total_reports: number;
  focus_states: string[];
  state_summaries: StateIntelligenceSummary[];
  recent_alerts: any[];
}

// Matches backend AegisScanResponse (from POST /api/aegis/scan)
export interface AegisScanResponse {
  scan_id: number;
  run_id: string;
  status: string;
  states_to_scan: string[];
  message: string;
}

// Matches backend AegisSynthesisResponse
export interface AegisSynthesisResponse {
  run_id: string;
  status: string;
  message: string;
}

// Matches backend AegisSimulationResponse
export interface AegisSimulationResponse {
  simulation_id: string;
  status: string;
  message: string;
}

// Matches backend AegisSimulationStatusResponse
export interface AegisSimulationStatusResponse {
  simulation_id: string;
  status: string;
  scan_id: number;
  created_at: string | null;
  scenario_json: Record<string, any> | null;
  projections_json: Record<string, any> | null;
  policy_brief_json: Record<string, any> | null;
  error: string | null;
}

// Matches backend AegisReportResponse
export interface AegisReportResponse {
  report_id: string;
  status: string;
  message: string;
  pdf_path: string | null;
  download_url: string | null;
}

// Matches backend AegisReportStatusResponse
export interface AegisReportStatusResponse {
  report_id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  pdf_path: string | null;
  download_url: string | null;
  steps_completed: string[];
  timings: Record<string, number>;
  error: string | null;
  states_analyzed: string[];
  sources_cited: number;
  infographics_generated: number;
}

// Matches backend AegisMarathonRunResponse
export interface AegisMarathonRunResponse {
  run_id: string;
  status: string;
  track_id: string;
  scan_id: number | null;
  day_date: string;
  mode: string;
  actions_taken: string[];
}

// Matches backend AegisDemoRunResponse
export interface AegisDemoRunResponse {
  run_id: string;
  status: string;
  track_id: string;
  period_key: string;
  message: string;
}

// Matches backend AegisMarathonDayResponse
export interface AegisMarathonDayResponse {
  id: number;
  track_id: string;
  day_date: string;
  scan_id: number;
  prev_scan_id: number | null;
  delta_json: Record<string, any> | null;
  continuity_note_json: Record<string, any> | null;
  thought_signature: string | null;
  prev_thought_signature: string | null;
  signature_short: string | null;
  prev_signature_short: string | null;
  thinking_level: string | null;
  actions_taken: string[] | null;
  simulation_triggered: string | null;
  report_triggered: string | null;
  created_at: string | null;
}

// Matches backend ContinuityChainEntry
export interface ContinuityChainEntry {
  day_date: string;
  thinking_level: string | null;
  summary: string;
  decision_explanation: string;
  predictions: string[];
  self_corrections: string[];
  actions_taken: string[];
  signature_linked: boolean;
}

// Matches backend AegisMarathonTimelineResponse
export interface AegisMarathonTimelineResponse {
  track_id: string;
  days: AegisMarathonDayResponse[];
  continuity_chain: ContinuityChainEntry[];
  total_days: number;
  total_self_corrections: number;
  total_actions: number;
}

// Matches backend AegisPipelineReadinessResponse
export interface AegisPipelineReadinessResponse {
  scan_id: number;
  scan_exists: boolean;
  scan_status: string;
  has_rollup_json: boolean;
  assessments_count: number;
  synthesis_ready: boolean;
  simulation_ready: boolean;
  report_ready: boolean;
  marathon_ready: boolean;
  missing_requirements: string[];
}

// Matches backend ReportsListResponse
export interface ReportListItem {
  filename: string;
  created_at: string;
  size_bytes: number;
  download_url: string;
}
