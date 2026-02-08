
import {
  JobResponse,
  JobEvent,
  JobEventsResponse,
  AegisDashboardResponse,
  AegisScanResponse,
  AegisScanStatusResponse,
  AegisSynthesisResponse,
  AegisSimulationResponse,
  AegisSimulationStatusResponse,
  AegisReportResponse,
  AegisReportStatusResponse,
  AegisMarathonRunResponse,
  AegisMarathonTimelineResponse,
  AegisDemoRunResponse,
  AegisPipelineReadinessResponse,
  ReportListItem,
} from '../types';
import { FARMA_API_KEY, API_BASE_URL, FOCUS_STATES } from '../constants';

const headers: Record<string, string> = {
  'Content-Type': 'application/json',
  'X-API-Key': FARMA_API_KEY,
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: { ...headers, ...options?.headers },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

async function requestBlob(path: string, options?: RequestInit): Promise<Blob> {
  const url = path.startsWith('http://') || path.startsWith('https://')
    ? path
    : `${API_BASE_URL}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: { ...headers, ...options?.headers },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.blob();
}

export const apiService = {
  // ── Job Tracking ──────────────────────────────────────────

  async getJobStatus(jobId: string): Promise<JobResponse> {
    return request<JobResponse>(`/api/jobs/${encodeURIComponent(jobId)}`);
  },

  async getJobEvents(jobId: string): Promise<JobEvent[]> {
    const resp = await request<JobEventsResponse>(`/api/jobs/${encodeURIComponent(jobId)}/events`);
    return resp.events;
  },

  // ── Farmer SMS ────────────────────────────────────────────

  async simulateFarmerSms(
    phone: string,
    message: string,
    useAegisContext: boolean = true
  ): Promise<JobResponse> {
    const params = new URLSearchParams({
      phone,
      message,
      use_aegis_context: String(useAegisContext),
    });
    return request<JobResponse>(`/api/farmer/simulate?${params.toString()}`, {
      method: 'POST',
    });
  },

  async resumeJob(jobId: string, responseText: string): Promise<JobResponse> {
    return request<JobResponse>(`/api/farmer/${encodeURIComponent(jobId)}/resume`, {
      method: 'POST',
      body: JSON.stringify({ response_text: responseText }),
    });
  },

  // ── AEGIS Dashboard ───────────────────────────────────────

  async getAegisDashboard(): Promise<AegisDashboardResponse> {
    return request<AegisDashboardResponse>('/api/aegis/dashboard');
  },

  // ── AEGIS Scan ────────────────────────────────────────────

  async startAegisScan(
    states: string[] = FOCUS_STATES,
    daysBack: number = 7,
    forceRefresh: boolean = false
  ): Promise<AegisScanResponse> {
    return request<AegisScanResponse>('/api/aegis/scan', {
      method: 'POST',
      body: JSON.stringify({
        states,
        days_back: daysBack,
        force_refresh: forceRefresh,
      }),
    });
  },

  async getScanStatus(scanId: string | number): Promise<AegisScanStatusResponse> {
    return request<AegisScanStatusResponse>(`/api/aegis/scan/${encodeURIComponent(scanId)}`);
  },

  // ── AEGIS Synthesis ───────────────────────────────────────

  async startSynthesis(scanId: number, states?: string[]): Promise<AegisSynthesisResponse> {
    return request<AegisSynthesisResponse>('/api/aegis/synthesis', {
      method: 'POST',
      body: JSON.stringify({ scan_id: scanId, states: states || null }),
    });
  },

  // ── AEGIS Simulation ──────────────────────────────────────

  async startSimulation(
    scanId: number,
    scenario: Record<string, any>
  ): Promise<AegisSimulationResponse> {
    return request<AegisSimulationResponse>('/api/aegis/simulations', {
      method: 'POST',
      body: JSON.stringify({ scan_id: scanId, scenario }),
    });
  },

  async getSimulationStatus(simulationId: string): Promise<AegisSimulationStatusResponse> {
    return request<AegisSimulationStatusResponse>(
      `/api/aegis/simulations/${encodeURIComponent(simulationId)}`
    );
  },

  // ── AEGIS Report ──────────────────────────────────────────

  async generateReport(
    scanId: number,
    states?: string[],
    includeInfographics: boolean = true,
    includeAnnexes: boolean = true,
    simulationId?: string
  ): Promise<AegisReportResponse> {
    return request<AegisReportResponse>('/api/aegis/report', {
      method: 'POST',
      body: JSON.stringify({
        scan_id: scanId,
        states: states || null,
        include_infographics: includeInfographics,
        include_annexes: includeAnnexes,
        simulation_id: simulationId || null,
      }),
    });
  },

  async getReportStatus(reportId: string): Promise<AegisReportStatusResponse> {
    return request<AegisReportStatusResponse>(
      `/api/aegis/report/${encodeURIComponent(reportId)}`
    );
  },

  async downloadReportPdf(reportId: string): Promise<Blob> {
    return requestBlob(`/api/aegis/report/${encodeURIComponent(reportId)}/download`);
  },

  async downloadByPath(downloadPath: string): Promise<Blob> {
    return requestBlob(downloadPath);
  },

  async listReports(): Promise<{ reports: ReportListItem[]; total: number }> {
    return request<{ reports: ReportListItem[]; total: number }>('/api/aegis/reports');
  },

  // ── AEGIS Marathon ────────────────────────────────────────

  async startMarathonRun(
    trackId: string,
    mode: 'manual' | 'autonomous' = 'manual',
    scanId?: number,
    dayDate?: string,
    prevScanId?: number
  ): Promise<AegisMarathonRunResponse> {
    return request<AegisMarathonRunResponse>('/api/aegis/marathon/run', {
      method: 'POST',
      body: JSON.stringify({
        track_id: trackId,
        mode,
        scan_id: scanId ?? null,
        day_date: dayDate ?? null,
        prev_scan_id: prevScanId ?? null,
      }),
    });
  },

  async getMarathonTimeline(trackId: string): Promise<AegisMarathonTimelineResponse> {
    return request<AegisMarathonTimelineResponse>(
      `/api/aegis/marathon/${encodeURIComponent(trackId)}/timeline`
    );
  },

  async getPipelineReadiness(scanId: number): Promise<AegisPipelineReadinessResponse> {
    return request<AegisPipelineReadinessResponse>(
      `/api/aegis/pipeline/readiness/${encodeURIComponent(scanId)}`
    );
  },

  async runDemo(
    options?: {
      track_id?: string;
      states?: string[];
      days_back?: number;
      force_refresh?: boolean;
      include_infographics?: boolean;
      include_annexes?: boolean;
      simulation_scenario?: Record<string, any>;
    }
  ): Promise<AegisDemoRunResponse> {
    return request<AegisDemoRunResponse>('/api/aegis/demo/run', {
      method: 'POST',
      body: JSON.stringify({
        track_id: options?.track_id ?? null,
        states: options?.states ?? null,
        days_back: options?.days_back ?? 7,
        force_refresh: options?.force_refresh ?? false,
        include_infographics: options?.include_infographics ?? false,
        include_annexes: options?.include_annexes ?? true,
        simulation_scenario: options?.simulation_scenario ?? null,
      }),
    });
  },
};
