
import React, { useState, useEffect, useMemo } from 'react';
import { apiService } from '../services/api';
import { Icons, FOCUS_STATES } from '../constants';
import { JobResponse, JobEvent, AegisScanStatusResponse, AegisSimulationStatusResponse, AegisReportStatusResponse, AegisMarathonTimelineResponse, AegisPipelineReadinessResponse, ReportListItem } from '../types';

const STORAGE_KEY = 'aegis_pipeline_state_v1';
const DEFAULT_MARATHON_TRACK_ID = 'demo-track';
const DEFAULT_SCAN_DAYS_BACK = 7;

function currentWeeklyTrackId() {
  return DEFAULT_MARATHON_TRACK_ID;
}

function currentWeeklyDayDate() {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun .. 6=Sat
  const offsetToMonday = (day + 6) % 7;
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  monday.setUTCDate(monday.getUTCDate() - offsetToMonday);
  return monday.toISOString().slice(0, 10);
}

function scanWindowLabel(daysBack: number): string {
  const now = new Date();
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - Math.max(daysBack - 1, 0));
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return `${fmt(start)} to ${fmt(end)} (UTC)`;
}

function scenarioSeverityLabel(scenario: Record<string, any> | null | undefined): string {
  if (!scenario || typeof scenario !== 'object') return '—';
  if (typeof scenario.severity === 'string' && scenario.severity.trim()) {
    return scenario.severity;
  }
  const intensity = Number(scenario.intensity);
  if (Number.isNaN(intensity)) return '—';
  if (intensity >= 1.8) return 'critical';
  if (intensity >= 1.4) return 'high';
  if (intensity >= 1.1) return 'medium';
  return 'low';
}

type PersistedPipelineState = {
  currentStage: number;
  activeJobId: string | null;
  scanId: number | null;
  scanRunId: string | null;
  simulationId: string | null;
  reportId: string | null;
  marathonTrackId: string;
};

function readPersistedState(): PersistedPipelineState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return {
      currentStage: Number(parsed.currentStage || 1),
      activeJobId: parsed.activeJobId || null,
      scanId: parsed.scanId != null ? Number(parsed.scanId) : null,
      scanRunId: parsed.scanRunId || null,
      simulationId: parsed.simulationId || null,
      reportId: parsed.reportId || null,
      marathonTrackId:
        (typeof parsed.marathonTrackId === 'string' && parsed.marathonTrackId.startsWith('demo-track'))
          ? DEFAULT_MARATHON_TRACK_ID
          : (parsed.marathonTrackId || currentWeeklyTrackId()),
    };
  } catch {
    return null;
  }
}

function clearPersistedState() {
  localStorage.removeItem(STORAGE_KEY);
}

export const AegisPipeline: React.FC = () => {
  const [currentStage, setCurrentStage] = useState(1);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobResponse | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Cross-stage state
  const [scanId, setScanId] = useState<number | null>(null);
  const [scanRunId, setScanRunId] = useState<string | null>(null);
  const [simulationId, setSimulationId] = useState<string | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const [marathonTrackId, setMarathonTrackId] = useState<string>(currentWeeklyTrackId());
  const [pipelineReadiness, setPipelineReadiness] = useState<AegisPipelineReadinessResponse | null>(null);
  const [hydrated, setHydrated] = useState(false);

  // Stage results
  const [scanResults, setScanResults] = useState<AegisScanStatusResponse | null>(null);
  const [simulationResults, setSimulationResults] = useState<AegisSimulationStatusResponse | null>(null);
  const [reportResults, setReportResults] = useState<AegisReportStatusResponse | null>(null);
  const [marathonResults, setMarathonResults] = useState<AegisMarathonTimelineResponse | null>(null);
  const [reportLibrary, setReportLibrary] = useState<ReportListItem[]>([]);
  const curatedReportLibrary = useMemo(() => {
    const sorted = [...(reportLibrary || [])].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
    if (sorted.length === 0) return [];

    const selected: ReportListItem[] = [];
    if (reportId) {
      const current = sorted.find((r) => (r.filename || '').includes(reportId));
      if (current) selected.push(current);
    }
    for (const r of sorted) {
      const exists = selected.some(
        (s) => s.filename === r.filename && s.created_at === r.created_at
      );
      if (!exists) {
        selected.push(r);
      }
      if (selected.length >= 2) break;
    }
    return selected;
  }, [reportLibrary, reportId]);

  useEffect(() => {
    const saved = readPersistedState();
    if (saved) {
      setCurrentStage(saved.currentStage || 1);
      setActiveJobId(saved.activeJobId || null);
      setScanId(saved.scanId ?? null);
      setScanRunId(saved.scanRunId || null);
      setSimulationId(saved.simulationId || null);
      setReportId(saved.reportId || null);
      setMarathonTrackId(saved.marathonTrackId || currentWeeklyTrackId());
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    const payload: PersistedPipelineState = {
      currentStage,
      activeJobId,
      scanId,
      scanRunId,
      simulationId,
      reportId,
      marathonTrackId,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  }, [hydrated, currentStage, activeJobId, scanId, scanRunId, simulationId, reportId, marathonTrackId]);

  useEffect(() => {
    if (!hydrated) return;
    refreshReportLibrary();
  }, [hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    let canceled = false;

    (async () => {
      if (scanId) {
        try {
          const scanRef = scanRunId || scanId;
          const status = await apiService.getScanStatus(scanRef);
          if (!canceled) {
            setScanResults(status);
            if (status.scan_id) {
              setScanId(status.scan_id);
            }
          }
          await ensureReadiness(scanId);
        } catch (e) {
          console.error('Persisted scan hydrate failed:', e);
        }
      }

      if (simulationId) {
        try {
          const sim = await apiService.getSimulationStatus(simulationId);
          if (!canceled) {
            setSimulationResults(sim);
          }
        } catch (e) {
          console.error('Persisted simulation hydrate failed:', e);
        }
      }

      if (reportId) {
        try {
          const report = await apiService.getReportStatus(reportId);
          if (!canceled) {
            setReportResults(report);
          }
          await refreshReportLibrary();
        } catch (e) {
          console.error('Persisted report hydrate failed:', e);
        }
      }

      if (marathonTrackId) {
        try {
          const timeline = await apiService.getMarathonTimeline(marathonTrackId);
          if (!canceled) {
            setMarathonResults(timeline);
          }
        } catch (e) {
          console.error('Persisted marathon hydrate failed:', e);
        }
      }
    })();

    return () => {
      canceled = true;
    };
  }, [hydrated, scanId, scanRunId, simulationId, reportId, marathonTrackId]);

  useEffect(() => {
    if (!scanId) {
      setPipelineReadiness(null);
      return;
    }
    let canceled = false;
    (async () => {
      try {
        const readiness = await apiService.getPipelineReadiness(scanId);
        if (!canceled) setPipelineReadiness(readiness);
      } catch (e) {
        console.error('Readiness fetch failed:', e);
      }
    })();
    return () => {
      canceled = true;
    };
  }, [scanId]);

  // Poll job status and events
  useEffect(() => {
    if (!activeJobId) return;

    const interval = setInterval(async () => {
      try {
        const [updatedJob, updatedEvents] = await Promise.all([
          apiService.getJobStatus(activeJobId),
          apiService.getJobEvents(activeJobId),
        ]);
        setJob(updatedJob);
        setEvents(updatedEvents);

        if (updatedJob.status === 'completed' || updatedJob.status === 'failed') {
          clearInterval(interval);
          if (updatedJob.status === 'completed') {
            if (updatedJob.job_type === 'aegis_demo') {
              await fetchDemoResults(updatedJob);
            } else {
              await fetchStageResults(currentStage, updatedJob);
            }
          }
          setActiveJobId(null);
        }
      } catch (e: any) {
        console.error('Poll error:', e);
      }
    }, 4000);

    return () => clearInterval(interval);
  }, [activeJobId, currentStage]);

  useEffect(() => {
    if (!hydrated || !activeJobId) return;
    let canceled = false;

    (async () => {
      try {
        const [updatedJob, updatedEvents] = await Promise.all([
          apiService.getJobStatus(activeJobId),
          apiService.getJobEvents(activeJobId),
        ]);
        if (canceled) return;
        setJob(updatedJob);
        setEvents(updatedEvents);
        if (updatedJob.status === 'completed') {
          if (updatedJob.job_type === 'aegis_demo') {
            await fetchDemoResults(updatedJob);
          } else {
            await fetchStageResults(currentStage, updatedJob);
          }
          setActiveJobId(null);
        } else if (updatedJob.status === 'failed') {
          setError((updatedJob.result?.error as string) || 'Last pipeline job failed.');
          setActiveJobId(null);
        }
      } catch (e) {
        if (canceled) return;
        console.error('Resume fetch failed:', e);
        setActiveJobId(null);
      }
    })();

    return () => {
      canceled = true;
    };
  }, [hydrated, activeJobId, currentStage]);

  const fetchStageResults = async (stage: number, completedJob: JobResponse) => {
    try {
      switch (stage) {
        case 1: {
          const runId = scanRunId || activeJobId;
          if (runId) {
            const status = await apiService.getScanStatus(runId);
            setScanResults(status);
            if (status.scan_id) setScanId(status.scan_id);
          }
          break;
        }
        case 2: {
          if (scanId) {
            await ensureReadiness(scanId);
          }
          break;
        }
        case 3: {
          if (simulationId) {
            const status = await apiService.getSimulationStatus(simulationId);
            setSimulationResults(status);
          }
          break;
        }
        case 4: {
          const completedReportId = completedJob.job_id || activeJobId || reportId;
          if (completedReportId) {
            setReportId(completedReportId);
            try {
              const status = await apiService.getReportStatus(completedReportId);
              setReportResults(status);
              setReportId(status.report_id);
            } catch (e) {
              console.error('Failed to fetch report status after completion:', e);
              setReportResults((prev) => prev || {
                report_id: completedReportId,
                status: 'completed',
                started_at: null,
                completed_at: null,
                pdf_path: null,
                download_url: null,
                steps_completed: [],
                timings: {},
                error: null,
                states_analyzed: [],
                sources_cited: 0,
                infographics_generated: 0,
              });
            }
            await refreshReportLibrary();
          }
          break;
        }
        case 5: {
          const trackId = String(completedJob.result?.track_id || marathonTrackId || DEFAULT_MARATHON_TRACK_ID);
          const timeline = await apiService.getMarathonTimeline(trackId);
          setMarathonTrackId(trackId);
          setMarathonResults(timeline);
          break;
        }
      }
    } catch (e: any) {
      console.error('Failed to fetch stage results:', e);
    }
  };

  const fetchDemoResults = async (completedJob: JobResponse) => {
    const result = completedJob.result || {};
    const resolvedTrack = String(result.track_id || marathonTrackId || currentWeeklyTrackId());
    setMarathonTrackId(resolvedTrack);

    const scanRun = String(result.scan_run_id || scanRunId || '');
    const resolvedScanId = Number(result.scan_id || scanId || 0);
    if (scanRun) {
      setScanRunId(scanRun);
      try {
        const status = await apiService.getScanStatus(scanRun);
        setScanResults(status);
        if (status.scan_id) setScanId(status.scan_id);
      } catch (e) {
        console.error('Failed to hydrate scan result from demo job:', e);
      }
    } else if (resolvedScanId > 0) {
      try {
        const status = await apiService.getScanStatus(resolvedScanId);
        setScanResults(status);
        if (status.scan_id) setScanId(status.scan_id);
      } catch (e) {
        console.error('Failed to hydrate scan result from demo job:', e);
      }
    }

    const simId = String(result.simulation_id || simulationId || '');
    if (simId) {
      setSimulationId(simId);
      try {
        const sim = await apiService.getSimulationStatus(simId);
        setSimulationResults(sim);
      } catch (e) {
        console.error('Failed to hydrate simulation result from demo job:', e);
      }
    }

    const reportId = String(result.report_id || '');
    if (reportId) {
      setReportId(reportId);
      try {
        const report = await apiService.getReportStatus(reportId);
        setReportResults(report);
        await refreshReportLibrary();
      } catch (e) {
        console.error('Failed to hydrate report result from demo job:', e);
      }
    }

    const timelineFromResult = (result.timeline && typeof result.timeline === 'object')
      ? (result.timeline as AegisMarathonTimelineResponse)
      : null;
    if (timelineFromResult) {
      setMarathonResults(timelineFromResult);
    }

    try {
      const timeline = await apiService.getMarathonTimeline(resolvedTrack);
      setMarathonResults(timeline);
    } catch (e) {
      console.error('Failed to hydrate marathon timeline from demo job:', e);
    }

  };

  const refreshReportLibrary = async () => {
    try {
      const resp = await apiService.listReports();
      setReportLibrary(resp.reports || []);
    } catch (e) {
      console.error('Failed to fetch reports library:', e);
    }
  };

  const ensureReadiness = async (targetScanId: number): Promise<AegisPipelineReadinessResponse | null> => {
    try {
      const readiness = await apiService.getPipelineReadiness(targetScanId);
      if (scanId && scanId === targetScanId) {
        setPipelineReadiness(readiness);
      }
      return readiness;
    } catch (e) {
      console.error('Readiness check failed:', e);
      return null;
    }
  };

  const resetSession = () => {
    setCurrentStage(1);
    setActiveJobId(null);
    setJob(null);
    setEvents([]);
    setError(null);
    setScanId(null);
    setScanRunId(null);
    setSimulationId(null);
    setReportId(null);
    setMarathonTrackId(currentWeeklyTrackId());
    setPipelineReadiness(null);
    setScanResults(null);
    setSimulationResults(null);
    setReportResults(null);
    setMarathonResults(null);
    clearPersistedState();
  };

  const runDemoCycle = async (forceRefresh: boolean = false) => {
    setError(null);
    setEvents([]);
    setJob(null);
    const track = marathonTrackId || currentWeeklyTrackId();
    setMarathonTrackId(track);
    setCurrentStage(5);

    try {
      const resp = await apiService.runDemo({
        track_id: track,
        states: FOCUS_STATES,
        days_back: DEFAULT_SCAN_DAYS_BACK,
        force_refresh: forceRefresh,
        include_infographics: true,
        include_annexes: true,
      });
      setMarathonTrackId(resp.track_id);
      setActiveJobId(resp.run_id);
      setJob({
        job_id: resp.run_id,
        job_type: 'aegis_demo',
        status: 'running',
        started_at: null,
        completed_at: null,
        result: null,
      });
    } catch (e: any) {
      setError(e.message || 'Failed to start demo orchestrator.');
    }
  };

  const stages = [
    { id: 1, name: 'SCAN', icon: Icons.Scan, desc: 'Intel collection' },
    { id: 2, name: 'SYNTHESIS', icon: Icons.Synthesis, desc: 'Risk fusion' },
    { id: 3, name: 'SIMULATION', icon: Icons.Simulation, desc: 'What-if modeling' },
    { id: 4, name: 'REPORT', icon: Icons.Report, desc: 'PDF generation' },
    { id: 5, name: 'MARATHON', icon: Icons.Marathon, desc: 'Continuity analysis' },
  ];

  const runStage = async (id: number, options?: { forceRefresh?: boolean }) => {
    setCurrentStage(id);
    setError(null);
    setEvents([]);
    setJob(null);

    try {
      switch (id) {
        case 1: {
          setSimulationId(null);
          setReportId(null);
          setSimulationResults(null);
          setReportResults(null);
          const resp = await apiService.startAegisScan(
            FOCUS_STATES,
            DEFAULT_SCAN_DAYS_BACK,
            Boolean(options?.forceRefresh)
          );
          setScanRunId(resp.run_id);
          setScanId(resp.scan_id);
          if (resp.status === 'completed') {
            setActiveJobId(null);
            setJob({
              job_id: resp.run_id,
              job_type: 'aegis_scan',
              status: 'completed',
              started_at: null,
              completed_at: null,
              result: { scan_id: resp.scan_id },
            });
            const status = await apiService.getScanStatus(resp.scan_id);
            setScanResults(status);
            await ensureReadiness(resp.scan_id);
          } else {
            setActiveJobId(resp.run_id);
            setJob({ job_id: resp.run_id, job_type: 'aegis_scan', status: 'running', started_at: null, completed_at: null, result: null });
          }
          break;
        }
        case 2: {
          if (!scanId) {
            setError('Run a Scan first (Stage 1) to get a scan_id.');
            return;
          }
          const resp = await apiService.startSynthesis(scanId);
          setActiveJobId(resp.run_id);
          setJob({ job_id: resp.run_id, job_type: 'aegis_synthesis', status: 'running', started_at: null, completed_at: null, result: null });
          break;
        }
        case 3: {
          if (!scanId) {
            setError('Run a Scan first (Stage 1) to get a scan_id.');
            return;
          }
          const readiness = await ensureReadiness(scanId);
          if (!readiness?.simulation_ready) {
            setError(`Simulation blocked: ${readiness?.missing_requirements?.join(', ') || 'synthesis is not ready for this scan'}.`);
            return;
          }
          const scenario = {
            crisis_type: 'conflict_escalation',
            intensity: 1.4,
            duration_days: 14,
            severity: 'high',
            geo_scope: { states: FOCUS_STATES },
            description: 'Simulated escalation scenario for humanitarian planning',
          };
          const resp = await apiService.startSimulation(scanId, scenario);
          setSimulationId(resp.simulation_id);
          setActiveJobId(resp.simulation_id);
          setJob({ job_id: resp.simulation_id, job_type: 'aegis_simulation', status: 'running', started_at: null, completed_at: null, result: null });
          break;
        }
        case 4: {
          if (!scanId) {
            setError('Run a Scan first (Stage 1) to get a scan_id.');
            return;
          }
          const readiness = await ensureReadiness(scanId);
          if (!readiness?.report_ready) {
            setError(`Report blocked: ${readiness?.missing_requirements?.join(', ') || 'synthesis is not ready for this scan'}.`);
            return;
          }
          const resp = await apiService.generateReport(scanId, undefined, true, true, simulationId || undefined);
          setReportId(resp.report_id);
          setActiveJobId(resp.report_id);
          setJob({ job_id: resp.report_id, job_type: 'aegis_report', status: 'running', started_at: null, completed_at: null, result: null });
          break;
        }
        case 5: {
          if (!scanId) {
            setError('Run Scan + Synthesis first so Marathon can append continuity.');
            return;
          }
          const readiness = await ensureReadiness(scanId);
          if (!readiness?.marathon_ready) {
            setError(`Marathon blocked: ${readiness?.missing_requirements?.join(', ') || 'run synthesis first'}.`);
            return;
          }

          const trackId = marathonTrackId || currentWeeklyTrackId();
          try {
            const existing = await apiService.getMarathonTimeline(trackId);
            if (existing.days && existing.days.length > 0) {
              setMarathonResults(existing);
              const latest = existing.days[existing.days.length - 1];
              if (Number(latest.scan_id) === Number(scanId)) {
                setJob({
                  job_id: `marathon-reuse-${latest.id}`,
                  job_type: 'aegis_marathon',
                  status: 'completed',
                  started_at: null,
                  completed_at: null,
                  result: {
                    track_id: trackId,
                    scan_id: scanId,
                    day_date: latest.day_date,
                    reused: true,
                  },
                });
                setActiveJobId(null);
                return;
              }
            }
          } catch (e) {
            console.error('Failed to load existing timeline before marathon run:', e);
          }
          const dayDate = currentWeeklyDayDate();
          setMarathonTrackId(trackId);
          const resp = await apiService.startMarathonRun(
            trackId,
            'manual',
            scanId,
            dayDate,
            undefined
          );
          setActiveJobId(resp.run_id);
          setJob({
            job_id: resp.run_id,
            job_type: 'aegis_marathon',
            status: 'running',
            started_at: null,
            completed_at: null,
            result: null,
          });
          break;
        }
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDownloadReport = async () => {
    const targetReportId = reportResults?.report_id || reportId;
    if (!targetReportId) return;
    try {
      const blob = await apiService.downloadReportPdf(targetReportId);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      const fallback = `aegis_report_${targetReportId}.pdf`;
      const rawPath = reportResults?.pdf_path;
      const safePath = typeof rawPath === 'string' ? rawPath : '';
      a.download = (safePath ? safePath.split('/').pop() : '') || fallback;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e: any) {
      setError(e.message || 'Failed to download report PDF.');
    }
  };

  const refreshCurrentReportStatus = async () => {
    const targetReportId = reportResults?.report_id || reportId;
    if (!targetReportId) return;
    try {
      const status = await apiService.getReportStatus(targetReportId);
      setReportResults(status);
      setReportId(status.report_id);
      await refreshReportLibrary();
    } catch (e: any) {
      setError(e.message || 'Failed to refresh report status.');
    }
  };

  const handleDownloadFromPath = async (downloadPath: string, fallbackFilename?: string) => {
    try {
      const blob = await apiService.downloadByPath(downloadPath);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = fallbackFilename || 'aegis_report.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e: any) {
      setError(e.message || 'Failed to download report.');
    }
  };

  const isStageComplete = (id: number) => {
    if (id === 1 && scanResults) return true;
    if (id === 2 && pipelineReadiness?.synthesis_ready) return true;
    if (id === 3 && simulationResults) return true;
    if (id === 4 && reportResults) return true;
    if (id === 5 && marathonResults) return true;
    return false;
  };

  const hasAnyStageResults = Boolean(scanResults || simulationResults || reportResults || marathonResults);

  const displayedEvents = useMemo(() => {
    const out: JobEvent[] = [];
    for (const e of events) {
      const key = `${e.status}|${e.message || e.event_type}`;
      const prev = out[out.length - 1];
      const prevKey = prev ? `${prev.status}|${prev.message || prev.event_type}` : '';
      if (key === prevKey) continue;
      out.push(e);
    }
    return out;
  }, [events]);

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-8">
      <div className="text-center space-y-2">
        <h2 className="text-3xl font-bold bg-gradient-to-r from-emerald-400 to-emerald-600 bg-clip-text text-transparent">
          AEGIS Intelligence Pipeline
        </h2>
        <p className="text-zinc-500 text-sm">Automated end-to-end humanitarian risk assessment framework</p>
        <p className="text-zinc-600 text-[10px] font-mono">Default scan window: {scanWindowLabel(DEFAULT_SCAN_DAYS_BACK)}</p>
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => runDemoCycle(false)}
            disabled={job?.status === 'running'}
            className={`text-[10px] font-mono uppercase tracking-wider px-3 py-1 rounded border transition-colors ${
              job?.status === 'running'
                ? 'border-zinc-800 text-zinc-600 cursor-not-allowed'
                : 'border-emerald-700 text-emerald-300 hover:text-emerald-100 hover:border-emerald-500'
            }`}
          >
            Run Demo
          </button>
          <button
            onClick={() => runDemoCycle(true)}
            disabled={job?.status === 'running'}
            className={`text-[10px] font-mono uppercase tracking-wider px-3 py-1 rounded border transition-colors ${
              job?.status === 'running'
                ? 'border-zinc-800 text-zinc-600 cursor-not-allowed'
                : 'border-cyan-700 text-cyan-300 hover:text-cyan-100 hover:border-cyan-500'
            }`}
          >
            Run Demo (Fresh Scan)
          </button>
          <button
            onClick={() => runStage(1, { forceRefresh: true })}
            disabled={job?.status === 'running'}
            className={`text-[10px] font-mono uppercase tracking-wider px-3 py-1 rounded border transition-colors ${
              job?.status === 'running'
                ? 'border-zinc-800 text-zinc-600 cursor-not-allowed'
                : 'border-cyan-700 text-cyan-300 hover:text-cyan-100 hover:border-cyan-500'
            }`}
          >
            Fresh Scan
          </button>
          <button
            onClick={resetSession}
            className="text-[10px] font-mono uppercase tracking-wider px-3 py-1 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 transition-colors"
          >
            Reset Session
          </button>
        </div>
      </div>

      {/* Pipeline Navigation */}
      <div className="flex flex-wrap justify-center gap-4">
        {stages.map((stage) => {
          const isActive = currentStage === stage.id;
          const isCompleted = isStageComplete(stage.id);
          return (
            <button
              key={stage.id}
              onClick={() => {
                if (isCompleted) {
                  setCurrentStage(stage.id);
                  setError(null);
                  return;
                }
                runStage(stage.id);
              }}
              disabled={job?.status === 'running'}
              className={`flex flex-col items-center p-4 min-w-[140px] rounded-2xl border transition-all duration-300 relative ${
                isActive
                  ? 'bg-emerald-600/10 border-emerald-500 text-emerald-500 shadow-lg shadow-emerald-900/20'
                  : isCompleted
                    ? 'bg-zinc-900 border-emerald-900/50 text-emerald-700'
                    : 'bg-zinc-900/50 border-zinc-800 text-zinc-600 hover:border-zinc-700'
              } ${job?.status === 'running' ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <div className="mb-3"><stage.icon /></div>
              <span className="text-[10px] font-bold uppercase tracking-widest">{stage.name}</span>
              <span className="text-[9px] opacity-60 mt-1">{stage.desc}</span>
              {isCompleted && (
                <div className="absolute top-2 right-2">
                  <svg className="w-3 h-3 text-emerald-500" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                  </svg>
                </div>
              )}
              {isActive && job?.status === 'running' && (
                <div className="absolute top-2 right-2">
                  <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-ping" />
                </div>
              )}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm text-center">
          {error}
        </div>
      )}

      {/* Main Content Area */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-zinc-900/50 border border-zinc-800 rounded-2xl p-8 min-h-[400px]">
          {!job && !hasAnyStageResults ? (
            <div className="h-full flex flex-col items-center justify-center text-center space-y-4">
               <div className="p-4 bg-zinc-800 rounded-full text-zinc-600">
                 <Icons.Scan />
               </div>
               <h3 className="text-lg font-bold">Initiate Pipeline Stage</h3>
               <p className="text-zinc-500 text-sm max-w-md">Select a stage to begin. AEGIS builds intelligence sequentially: Scan → Synthesis → Simulation → Report → Marathon.</p>
               <button
                onClick={() => runStage(1)}
                className="px-8 py-3 bg-emerald-600 hover:bg-emerald-500 rounded-xl font-bold text-sm transition-all"
               >
                 START SCAN STAGE
               </button>
               <button
                onClick={() => runStage(1, { forceRefresh: true })}
                className="px-8 py-3 bg-cyan-700 hover:bg-cyan-600 rounded-xl font-bold text-sm transition-all"
               >
                 START FRESH SCAN
               </button>
            </div>
          ) : (
            <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
              <div className="flex justify-between items-center pb-4 border-b border-zinc-800">
                <h3 className="font-bold flex items-center gap-2">
                  <span className="px-2 py-0.5 bg-zinc-800 rounded text-[10px] uppercase">{stages[currentStage - 1]?.name}</span>
                  STAGE {currentStage} {job?.status === 'completed' ? 'RESULTS' : 'PROCESSING'}
                </h3>
                <span className="text-zinc-500 font-mono text-xs">{activeJobId}</span>
              </div>

              {job?.status === 'running' ? (
                <div className="space-y-8 py-8">
                   <div className="max-w-xs mx-auto text-center space-y-4">
                     <div className="w-16 h-16 mx-auto border-4 border-emerald-500/30 border-t-emerald-500 rounded-full animate-spin" />
                     <p className="text-zinc-400 text-xs">Processing... {events.length} events received</p>
                   </div>
                   <div className="space-y-3 max-h-48 overflow-y-auto">
                      {displayedEvents.slice(-5).map((e) => (
                        <div key={e.event_id} className="flex gap-3 text-[11px] text-zinc-400 font-mono">
                          <span className="text-emerald-500 shrink-0">
                            {e.status === 'completed' ? '✓' : e.status === 'failed' ? '✗' : '→'}
                          </span>
                          <span>{e.message || e.event_type}</span>
                        </div>
                      ))}
                   </div>
                </div>
              ) : job?.status === 'failed' ? (
                <div className="p-6 bg-red-500/5 border border-red-500/20 rounded-xl text-center">
                  <p className="text-red-400 text-sm">{job.result?.error || 'Stage failed. Check events for details.'}</p>
                </div>
              ) : (
                <div className="space-y-6">
                  {/* Stage 1: Scan Results */}
                  {currentStage === 1 && scanResults && (
                    <div>
                      <div className="flex gap-4 mb-4 text-[10px] font-mono text-zinc-500">
                        <span>States: {scanResults.states_scanned}</span>
                        <span>Events: {scanResults.total_events}</span>
                        <span>Fatalities: {scanResults.total_fatalities}</span>
                        <span>Scan ID: {scanResults.scan_id}</span>
                      </div>
                      {scanResults.state_summaries && scanResults.state_summaries.length > 0 ? (
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                          {scanResults.state_summaries.map((s) => (
                            <div key={s.state_name} className="p-4 bg-zinc-950/50 border border-zinc-800 rounded-xl hover:border-emerald-900 transition-colors">
                              <div className="flex justify-between items-start mb-3">
                                <h4 className="font-bold text-sm">{s.state_name}</h4>
                                {s.priority_level && (
                                  <span className={`text-[9px] font-bold px-2 py-0.5 rounded ${
                                    s.priority_level === 'CRITICAL' ? 'bg-red-500/20 text-red-400' :
                                    s.priority_level === 'HIGH' ? 'bg-orange-500/20 text-orange-400' :
                                    'bg-zinc-800 text-zinc-400'
                                  }`}>
                                    {s.priority_level}
                                  </span>
                                )}
                              </div>
                              <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
                                <div className="text-zinc-500">CONFLICTS:</div>
                                <div className="text-red-400">{s.conflict_events}</div>
                                <div className="text-zinc-500">DISPLACED:</div>
                                <div className="text-emerald-400">{(s.idp_estimate || 0).toLocaleString()}</div>
                                <div className="text-zinc-500">FOOD:</div>
                                <div className="text-yellow-400">{s.food_insecurity_level}</div>
                                <div className="text-zinc-500">IPC PHASE:</div>
                                <div className="text-zinc-300">{s.ipc_phase || '—'}</div>
                                <div className="text-zinc-500">MARKETS:</div>
                                <div className="text-zinc-300">{s.markets_operational}</div>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-zinc-500 text-sm">Scan completed but no state summaries available.</p>
                      )}
                    </div>
                  )}

                  {/* Stage 2: Synthesis Results */}
                  {currentStage === 2 && job?.status === 'completed' && (
                    <div className="p-6 bg-emerald-500/5 border border-emerald-500/20 rounded-xl text-center space-y-3">
                      <div className="text-emerald-500 text-lg font-bold">Synthesis Complete</div>
                      <p className="text-zinc-400 text-sm">Risk assessments have been fused and analyzed. Proceed to Simulation (Stage 3) for what-if projections.</p>
                      <div className="space-y-2 text-[11px] font-mono max-h-48 overflow-y-auto text-left">
                        {displayedEvents.map(e => (
                          <div key={e.event_id} className="text-zinc-400">
                            <span className="text-emerald-500">✓</span> {e.message || e.event_type}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Stage 3: Simulation Results */}
                  {currentStage === 3 && simulationResults && (
                    <div className="space-y-4">
                      {/* Scenario header */}
                      <div className="p-4 bg-emerald-500/5 border border-emerald-500/20 rounded-xl">
                        <div className="text-emerald-500 text-sm font-bold mb-1">Crisis Simulation Complete</div>
                        {simulationResults.scenario_json && (
                          <div className="flex gap-3 text-[10px] font-mono text-zinc-500 mt-2">
                            <span>Type: {simulationResults.scenario_json.crisis_type || simulationResults.scenario_json.type || '—'}</span>
                            <span>Severity: {scenarioSeverityLabel(simulationResults.scenario_json)}</span>
                          </div>
                        )}
                      </div>

                      {/* Projections */}
                      {simulationResults.projections_json && (() => {
                        const p = simulationResults.projections_json;
                        const h = p.humanitarian || {};
                        const f = p.financial || {};
                        const noGo = h.no_go;
                        return (
                          <div className="space-y-3">
                            <div className="text-[10px] font-bold text-zinc-500 uppercase tracking-wider">Humanitarian Projections</div>
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                              <div className="p-3 bg-zinc-950 border border-zinc-800 rounded-lg">
                                <div className="text-[9px] text-zinc-500 uppercase">Baseline IDPs</div>
                                <div className="text-sm font-bold text-zinc-200 mt-1">{(h.baseline_idp || 0).toLocaleString()}</div>
                              </div>
                              <div className={`p-3 bg-zinc-950 border rounded-lg ${(h.idp_delta || 0) > 0 ? 'border-red-500/40' : 'border-emerald-500/40'}`}>
                                <div className="text-[9px] text-zinc-500 uppercase">IDP Change</div>
                                <div className={`text-sm font-bold mt-1 ${(h.idp_delta || 0) > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                                  {(h.idp_delta || 0) > 0 ? '+' : ''}{(h.idp_delta || 0).toLocaleString()}
                                </div>
                              </div>
                              <div className="p-3 bg-zinc-950 border border-zinc-800 rounded-lg">
                                <div className="text-[9px] text-zinc-500 uppercase">Food Need (MT)</div>
                                <div className="text-sm font-bold text-zinc-200 mt-1">{(h.food_mt || 0).toLocaleString()}</div>
                              </div>
                              <div className="p-3 bg-zinc-950 border border-amber-500/40 rounded-lg">
                                <div className="text-[9px] text-zinc-500 uppercase">Funding Gap</div>
                                <div className="text-sm font-bold text-amber-400 mt-1">${(h.funding_gap_usd || 0).toLocaleString()}</div>
                              </div>
                            </div>
                            <div className="flex gap-2 flex-wrap">
                              {noGo && <span className="text-[9px] bg-red-500/20 text-red-400 border border-red-500/30 px-2 py-0.5 rounded font-mono font-bold">NO-GO ZONE</span>}
                              {h.route_risk_score != null && (
                                <span className={`text-[9px] px-2 py-0.5 rounded font-mono ${h.route_risk_score > 0.15 ? 'bg-red-500/20 text-red-400' : 'bg-zinc-800 text-zinc-400'}`}>
                                  Route Risk: {(h.route_risk_score * 100).toFixed(0)}%
                                </span>
                              )}
                              {f.portfolio_risk_delta != null && (
                                <span className="text-[9px] bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded font-mono">
                                  Portfolio Risk: +{(f.portfolio_risk_delta * 100).toFixed(0)}%
                                </span>
                              )}
                            </div>
                            {f.loan_policy_actions && f.loan_policy_actions.length > 0 && (
                              <div className="space-y-1">
                                <div className="text-[9px] text-zinc-500 font-bold uppercase">Loan Policy Actions</div>
                                {f.loan_policy_actions.map((a: any, j: number) => (
                                  <div key={j} className="flex items-start gap-2 text-[10px]">
                                    <span className="text-amber-400 font-mono font-bold shrink-0">{(a.action || '').replace(/_/g, ' ')}</span>
                                    <span className="text-zinc-400">{a.reason}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })()}

                      {/* Policy Brief */}
                      {simulationResults.policy_brief_json && (() => {
                        const pb = simulationResults.policy_brief_json;
                        return (
                          <div className="space-y-3">
                            <div className="text-[10px] font-bold text-zinc-500 uppercase tracking-wider">Policy Brief</div>
                            {pb.summary && <p className="text-zinc-300 text-xs leading-relaxed">{pb.summary}</p>}
                            {pb.ranked_recommendations && pb.ranked_recommendations.length > 0 && (
                              <div className="space-y-2">
                                <div className="text-[9px] text-zinc-500 font-bold uppercase">Recommendations</div>
                                {pb.ranked_recommendations.map((rec: any, j: number) => (
                                  <div key={j} className="p-3 bg-zinc-950/50 border border-zinc-800 rounded-lg">
                                    <div className="flex justify-between items-start">
                                      <span className="text-xs font-bold text-zinc-200">{j + 1}. {rec.title}</span>
                                      {rec.owner && <span className="text-[8px] bg-zinc-800 text-zinc-500 px-1.5 py-0.5 rounded shrink-0">{rec.owner}</span>}
                                    </div>
                                    <p className="text-[10px] text-zinc-400 mt-1 leading-relaxed">{rec.detail}</p>
                                  </div>
                                ))}
                              </div>
                            )}
                            {pb.humanitarian_notes && pb.humanitarian_notes.length > 0 && (
                              <div>
                                <div className="text-[9px] text-zinc-500 font-bold uppercase mb-1">Humanitarian Notes</div>
                                {pb.humanitarian_notes.map((n: string, j: number) => (
                                  <div key={j} className="flex items-start gap-1.5 text-[10px] text-zinc-400 mb-0.5">
                                    <span className="text-zinc-600 mt-0.5">&bull;</span><span>{n}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                            {pb.farma_policy_notes && pb.farma_policy_notes.length > 0 && (
                              <div>
                                <div className="text-[9px] text-zinc-500 font-bold uppercase mb-1">FARMA Policy Notes</div>
                                {pb.farma_policy_notes.map((n: string, j: number) => (
                                  <div key={j} className="flex items-start gap-1.5 text-[10px] text-zinc-400 mb-0.5">
                                    <span className="text-zinc-600 mt-0.5">&bull;</span><span>{n}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                            {pb.confidence != null && (
                              <div className="text-[9px] text-zinc-600 font-mono">Confidence: {(pb.confidence * 100).toFixed(0)}%</div>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  )}

                  {/* Stage 4: Report Results */}
                  {currentStage === 4 && (
                    <div className="flex flex-col items-center justify-center py-12 space-y-4 w-full">
                      <div className="p-4 bg-emerald-500/10 rounded-full text-emerald-500">
                        <Icons.Report />
                      </div>
                      <h4 className="text-lg font-bold">Report Ready</h4>
                      {reportResults ? (
                        <>
                          {Array.isArray(reportResults.states_analyzed) && reportResults.states_analyzed.length > 0 && (
                            <p className="text-zinc-500 text-sm">
                              Analyzed {reportResults.states_analyzed.join(', ')} | {reportResults.sources_cited} sources cited | {reportResults.infographics_generated} infographics
                            </p>
                          )}
                          <button
                            onClick={handleDownloadReport}
                            className="px-6 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-bold transition-colors text-white"
                          >
                            DOWNLOAD PDF INTEL
                          </button>
                          {reportResults.error && (
                            <p className="text-red-400 text-xs">{reportResults.error}</p>
                          )}
                        </>
                      ) : (
                        <div className="text-center space-y-2">
                          <p className="text-zinc-500 text-sm">
                            Report job finished but status payload is not loaded yet.
                          </p>
                          <button
                            onClick={refreshCurrentReportStatus}
                            className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-[11px] font-bold text-zinc-100 transition-colors"
                          >
                            Refresh Report Status
                          </button>
                        </div>
                      )}
                      {curatedReportLibrary.length > 0 && (
                        <div className="w-full max-w-2xl mt-6 p-4 bg-zinc-950/50 border border-zinc-800 rounded-xl">
                          <div className="text-[10px] font-bold text-zinc-500 uppercase tracking-wider mb-3">
                            Report Library ({curatedReportLibrary.length})
                          </div>
                          <div className="space-y-2 max-h-48 overflow-y-auto">
                            {curatedReportLibrary.map((r) => (
                              <div key={`${r.filename}-${r.created_at}`} className="flex items-center justify-between gap-3 p-2 bg-zinc-900/70 border border-zinc-800 rounded">
                                <div className="min-w-0">
                                  <div className="text-[11px] text-zinc-200 truncate">{r.filename}</div>
                                  <div className="text-[10px] text-zinc-500">
                                    {new Date(r.created_at).toLocaleString()} | {(r.size_bytes / 1024).toFixed(0)} KB
                                  </div>
                                </div>
                                <button
                                  onClick={() => handleDownloadFromPath(r.download_url, r.filename)}
                                  className="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 rounded text-[10px] font-bold text-white shrink-0"
                                >
                                  Download
                                </button>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Stage 5: Marathon Results */}
                  {currentStage === 5 && marathonResults && (
                    <div className="space-y-4">
                      <div className="flex gap-4 text-[10px] font-mono text-zinc-500">
                        <span>Track: {marathonResults.track_id}</span>
                        <span>Checkpoints: {marathonResults.total_days}</span>
                        <span>Self-corrections: {marathonResults.total_self_corrections}</span>
                        <span>Actions: {marathonResults.total_actions}</span>
                      </div>
                      {marathonResults.continuity_chain.length > 0 ? (
                        <div className="space-y-3">
                          {marathonResults.continuity_chain.map((entry, i) => {
                            const level = (entry.thinking_level || '').toLowerCase();
                            const borderColor = level === 'high' ? 'border-red-500/60' : level === 'medium' ? 'border-amber-500/60' : 'border-emerald-500/40';
                            const levelBg = level === 'high' ? 'bg-red-500/20 text-red-400' : level === 'medium' ? 'bg-amber-500/20 text-amber-400' : 'bg-emerald-500/20 text-emerald-400';
                            const accentBar = level === 'high' ? 'bg-red-500' : level === 'medium' ? 'bg-amber-500' : 'bg-emerald-500';

                            return (
                              <div key={i} className={`relative p-4 bg-zinc-950/50 border ${borderColor} rounded-xl overflow-hidden`}>
                                {/* Left accent bar */}
                                <div className={`absolute left-0 top-0 bottom-0 w-1 ${accentBar} rounded-l-xl`} />

                                {/* Header: date + badges */}
                                <div className="flex justify-between items-center mb-2 pl-2">
                                  <div className="flex items-center gap-2">
                                    <span className="text-xs font-bold">Checkpoint {i + 1}</span>
                                    <span className="text-[10px] text-zinc-500 font-mono">{entry.day_date}</span>
                                  </div>
                                  <div className="flex gap-2 flex-wrap justify-end">
                                    {entry.signature_linked && (
                                      <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded font-mono">LINKED</span>
                                    )}
                                    {entry.thinking_level && (
                                      <span className={`text-[9px] ${levelBg} px-2 py-0.5 rounded font-mono uppercase`}>{entry.thinking_level}</span>
                                    )}
                                  </div>
                                </div>

                                {/* Decision explanation — agent's first-person reasoning */}
                                {entry.decision_explanation && (
                                  <div className="pl-2 mb-2 border-l-2 border-zinc-700 ml-1">
                                    <p className="text-zinc-300 text-xs italic leading-relaxed">{entry.decision_explanation}</p>
                                  </div>
                                )}

                                {/* Summary */}
                                {entry.summary && !entry.decision_explanation && (
                                  <p className="text-zinc-300 text-xs mb-2 pl-2">{entry.summary}</p>
                                )}

                                {/* Self-corrections — the "honesty" proof */}
                                {entry.self_corrections && entry.self_corrections.length > 0 && (
                                  <div className="pl-2 mb-2">
                                    <span className="text-[9px] text-amber-400 font-bold uppercase tracking-wider">Self-corrections</span>
                                    <div className="mt-1 space-y-1">
                                      {entry.self_corrections.map((corr: string, j: number) => (
                                        <div key={j} className="flex items-start gap-1.5">
                                          <span className="text-amber-400 text-[10px] mt-0.5">~</span>
                                          <span className="text-zinc-400 text-[10px] leading-relaxed">{corr}</span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                {/* Predictions */}
                                {entry.predictions && entry.predictions.length > 0 && (
                                  <div className="pl-2 mb-2">
                                    <span className="text-[9px] text-zinc-500 font-bold uppercase tracking-wider">Predictions</span>
                                    <div className="mt-1 space-y-1">
                                      {entry.predictions.map((pred: string, j: number) => (
                                        <div key={j} className="flex items-start gap-1.5">
                                          <span className="text-zinc-500 text-[10px] mt-0.5">&rarr;</span>
                                          <span className="text-zinc-400 text-[10px] leading-relaxed">{pred}</span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                {/* Actions taken — autonomy proof */}
                                {entry.actions_taken && entry.actions_taken.length > 0 && (
                                  <div className="pl-2 flex gap-2 flex-wrap mt-2">
                                    {entry.actions_taken.map((action: string, j: number) => {
                                      const actionLabel = action.replace(/_/g, ' ').replace('enqueue ', '').toUpperCase();
                                      const actionColor = action.includes('report') ? 'bg-purple-500/20 text-purple-400 border-purple-500/30' : 'bg-blue-500/20 text-blue-400 border-blue-500/30';
                                      return (
                                        <span key={j} className={`text-[8px] ${actionColor} border px-2 py-0.5 rounded font-mono font-bold tracking-wider`}>
                                          {actionLabel}
                                        </span>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <p className="text-zinc-500 text-sm">Marathon run completed. No continuity chain entries yet.</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="space-y-6">
          {/* Pipeline State */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6">
            <h4 className="text-xs font-bold text-zinc-500 uppercase mb-4 tracking-widest">Pipeline State</h4>
            <div className="space-y-4">
              <div className="flex justify-between items-center pb-3 border-b border-zinc-800">
                <span className="text-xs text-zinc-400">Scan ID</span>
                <span className="text-xs font-bold font-mono">{scanId || '—'}</span>
              </div>
              <div className="flex justify-between items-center pb-3 border-b border-zinc-800">
                <span className="text-xs text-zinc-400">Scan Run</span>
                <span className="text-xs font-bold font-mono text-emerald-500">{scanRunId || '—'}</span>
              </div>
              <div className="flex justify-between items-center pb-3 border-b border-zinc-800">
                <span className="text-xs text-zinc-400">Simulation</span>
                <span className="text-xs font-bold font-mono">{simulationId || '—'}</span>
              </div>
              <div className="flex justify-between items-center pb-3 border-b border-zinc-800">
                <span className="text-xs text-zinc-400">Report ID</span>
                <span className="text-xs font-bold font-mono">{reportId || '—'}</span>
              </div>
              <div className="flex justify-between items-center pb-3 border-b border-zinc-800">
                <span className="text-xs text-zinc-400">Current Job</span>
                <span className={`text-xs font-bold ${
                  job?.status === 'running' ? 'text-blue-400' :
                  job?.status === 'completed' ? 'text-emerald-400' :
                  job?.status === 'failed' ? 'text-red-400' :
                  'text-zinc-500'
                }`}>
                  {job?.status?.toUpperCase() || 'IDLE'}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-zinc-400">Events</span>
                <span className="text-xs font-bold">{events.length}</span>
              </div>
              {pipelineReadiness && (
                <div className="pt-3 border-t border-zinc-800">
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-zinc-400">Synthesis Ready</span>
                    <span className={`text-xs font-bold ${pipelineReadiness.synthesis_ready ? 'text-emerald-400' : 'text-amber-400'}`}>
                      {pipelineReadiness.synthesis_ready ? 'YES' : 'NO'}
                    </span>
                  </div>
                  {!pipelineReadiness.synthesis_ready && pipelineReadiness.missing_requirements.length > 0 && (
                    <p className="text-[10px] text-zinc-500 mt-1">{pipelineReadiness.missing_requirements.join(', ')}</p>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Event Log */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6">
            <h4 className="text-xs font-bold text-zinc-500 uppercase mb-4 tracking-widest">Recent Events</h4>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {displayedEvents.length > 0 ? (
                displayedEvents.slice(-8).map((e) => (
                  <div key={e.event_id} className="text-[10px] font-mono text-zinc-400 flex gap-2">
                    <span className={
                      e.status === 'completed' ? 'text-emerald-500' :
                      e.status === 'failed' ? 'text-red-500' :
                      'text-zinc-600'
                    }>
                      {e.status === 'completed' ? '✓' : e.status === 'failed' ? '✗' : '→'}
                    </span>
                    <span className="truncate">{e.message || e.event_type}</span>
                  </div>
                ))
              ) : (
                <p className="text-zinc-600 text-[10px] italic">No events yet</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
