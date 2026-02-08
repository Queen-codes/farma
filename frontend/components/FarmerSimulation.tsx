
import React, { useState, useEffect, useRef } from 'react';
import { apiService } from '../services/api';
import { JobResponse, JobEvent } from '../types';

export const FarmerSimulation: React.FC = () => {
  const [phone, setPhone] = useState('+234 80');
  const [message, setMessage] = useState('');
  const [useAegisContext, setUseAegisContext] = useState(true);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobResponse | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [agentResponse, setAgentResponse] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

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
        }
      } catch (e: any) {
        console.error('Poll error:', e);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [activeJobId]);

  useEffect(() => {
    scrollToBottom();
  }, [events]);

  const handleSimulateSms = async (content?: string) => {
    const finalMsg = content || message;
    if (!finalMsg) return;
    setError(null);
    setSubmitting(true);
    try {
      const resp = await apiService.simulateFarmerSms(
        phone.replace(/\s/g, ''),
        finalMsg,
        useAegisContext
      );
      setActiveJobId(resp.job_id);
      setJob(resp);
      setEvents([]);
      setMessage('');
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleResume = async () => {
    if (!activeJobId || !agentResponse) return;
    setError(null);
    try {
      const resp = await apiService.resumeJob(activeJobId, agentResponse);
      setJob(resp);
      setAgentResponse('');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleReset = () => {
    setActiveJobId(null);
    setJob(null);
    setEvents([]);
    setError(null);
  };

  const templates = [
    { label: 'Disease Report', text: 'My tomatoes in Kano have brown spots on the leaves and they are curling. The fruits are rotting too. What should I do?' },
    { label: 'Climate Advisory', text: 'I want to plant rice on my farm in Kura, Kano state. Will there be rain this week? When is best time to plant?' },
    { label: 'Loan Request', text: 'I want to borrow 50,000 naira for my rice farm near Anam River in Anambra state. I need it for fertilizer and seeds.' },
  ];

  // Derive progress from events
  const latestProgress = events.length > 0
    ? events.reduce((max, e) => Math.max(max, e.progress || 0), 0)
    : 0;

  // Map backend event status to display color
  const eventColor = (evt: JobEvent) => {
    if (evt.status === 'completed') return 'border-emerald-500';
    if (evt.status === 'failed') return 'border-red-500';
    if (evt.event_type?.includes('warning') || evt.event_type?.includes('escalat')) return 'border-orange-500';
    return 'border-zinc-800';
  };

  const eventTextColor = (evt: JobEvent) => {
    if (evt.status === 'completed') return 'text-emerald-400';
    if (evt.status === 'failed') return 'text-red-400';
    if (evt.event_type?.includes('warning') || evt.event_type?.includes('escalat')) return 'text-orange-300';
    return 'text-zinc-300';
  };

  return (
    <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-8 max-w-7xl mx-auto animate-in fade-in duration-500">
      {/* Simulation Input */}
      <div className="space-y-6">
        <div className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-6 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 right-0 w-32 h-32 bg-emerald-500/5 blur-3xl rounded-full" />

          <div className="flex justify-between items-center mb-6">
            <div>
              <h2 className="text-xl font-bold text-white tracking-tight">Farmer Communication Portal</h2>
              <p className="text-zinc-500 text-[11px] uppercase tracking-widest font-bold">SMS Simulation</p>
            </div>
            {activeJobId && (
              <button
                onClick={handleReset}
                className="px-3 py-1.5 text-[10px] font-bold text-zinc-400 bg-zinc-800 rounded-lg hover:bg-zinc-700 transition-colors"
              >
                NEW SESSION
              </button>
            )}
          </div>

          {error && (
            <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs">
              {error}
            </div>
          )}

            <div className="space-y-6">
              <div>
                <label className="block text-[10px] font-bold text-zinc-500 uppercase mb-2">Farmer Phone Number</label>
              <input
                type="text"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-sm focus:border-emerald-500 outline-none transition-all placeholder:text-zinc-700"
                placeholder="+234 800 000 0000"
              />
            </div>

            <div className="flex items-center justify-between p-3 rounded-xl border border-zinc-800 bg-zinc-950/60">
              <div>
                <div className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">Use AEGIS Intelligence</div>
                <div className="text-[10px] text-zinc-600 mt-1">
                  Toggle to compare underwriting with and without regional risk context.
                </div>
              </div>
              <button
                type="button"
                onClick={() => setUseAegisContext(v => !v)}
                disabled={submitting || (!!activeJobId && job?.status === 'running')}
                className={`relative w-14 h-8 rounded-full border transition-colors disabled:opacity-50 ${
                  useAegisContext
                    ? 'bg-emerald-600/20 border-emerald-500/60'
                    : 'bg-zinc-800 border-zinc-700'
                }`}
                aria-label="Toggle AEGIS context for underwriting"
              >
                <span
                  className={`absolute top-1 h-6 w-6 rounded-full transition-all ${
                    useAegisContext ? 'left-7 bg-emerald-400' : 'left-1 bg-zinc-400'
                  }`}
                />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-[10px] font-bold text-zinc-500 uppercase mb-2">Select Message Intent</label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-4">
                  {templates.map((t) => (
                    <button
                      key={t.label}
                      onClick={() => handleSimulateSms(t.text)}
                      disabled={submitting || (!!activeJobId && job?.status === 'running')}
                      className="p-3 bg-zinc-950 border border-zinc-800 rounded-xl text-left hover:border-emerald-600/50 transition-all group disabled:opacity-40"
                    >
                      <div className="text-[10px] font-bold text-emerald-500 group-hover:text-emerald-400 transition-colors uppercase mb-1">{t.label}</div>
                      <div className="text-[10px] text-zinc-500 line-clamp-2">Quick send template</div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="relative">
                <label className="block text-[10px] font-bold text-zinc-500 uppercase mb-2">Or type custom SMS</label>
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={4}
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-sm focus:border-emerald-500 outline-none transition-all resize-none"
                  placeholder="Enter manual message content..."
                />
              </div>

              <button
                onClick={() => handleSimulateSms()}
                disabled={submitting || !message || (!!activeJobId && job?.status === 'running')}
                className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-30 text-white font-bold py-3.5 rounded-xl text-xs transition-all shadow-xl shadow-emerald-900/10 flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                </svg>
                {submitting ? 'SENDING...' : 'EXECUTE SMS SIMULATION'}
              </button>
            </div>
          </div>
        </div>

        {job?.status === 'awaiting_human' && (
          <div className="bg-orange-500/10 border border-orange-500/20 rounded-2xl p-6 animate-in zoom-in duration-300 relative">
             <div className="absolute top-2 right-2 flex gap-1">
                <span className="w-1 h-1 bg-orange-500 rounded-full animate-ping" />
                <span className="w-1 h-1 bg-orange-500 rounded-full animate-ping delay-100" />
             </div>
            <h3 className="text-orange-400 text-[10px] font-bold tracking-widest uppercase flex items-center gap-2 mb-3">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              Escalated for Agent Action
            </h3>
            <p className="text-zinc-400 text-xs mb-4">This request requires human agent review before proceeding.</p>
            <textarea
              value={agentResponse}
              onChange={(e) => setAgentResponse(e.target.value)}
              className="w-full bg-zinc-950/50 border border-zinc-700 rounded-xl px-4 py-3 text-xs mb-3 outline-none focus:border-orange-500 transition-all"
              placeholder="e.g., Loan approved based on 2023 harvest history. Disbursement authorized."
            />
            <button
              onClick={handleResume}
              className="w-full bg-orange-600 hover:bg-orange-500 text-white font-bold py-2.5 rounded-lg text-[10px] uppercase tracking-wider transition-all"
            >
              FINALIZE AGENT DECISION
            </button>
          </div>
        )}
      </div>

      {/* Monitoring Panel */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl flex flex-col shadow-2xl overflow-hidden min-h-[500px]">
        <div className="p-4 border-b border-zinc-800 flex items-center justify-between bg-zinc-950/50">
          <div className="flex items-center gap-3">
            <div className={`w-2 h-2 rounded-full ${job?.status === 'running' ? 'bg-blue-500 animate-pulse' : job?.status === 'completed' ? 'bg-emerald-500' : job?.status === 'failed' ? 'bg-red-500' : 'bg-emerald-500'}`} />
            <h3 className="text-[11px] font-bold font-mono text-zinc-400 tracking-tighter">{activeJobId || 'AWAITING_INPUT'}</h3>
          </div>
          {job && (
            <div className="flex gap-2">
               <span className="text-[9px] font-bold text-zinc-600 font-mono">STATUS:</span>
               <span className={`text-[9px] font-bold uppercase ${
                job.status === 'completed' ? 'text-emerald-400' :
                job.status === 'running' ? 'text-blue-400' :
                job.status === 'failed' ? 'text-red-400' :
                'text-orange-400'
              }`}>
                {job.status}
              </span>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-4 font-mono text-[11px] bg-zinc-950/20">
          {!job ? (
            <div className="h-full flex flex-col items-center justify-center text-zinc-600 italic space-y-3 opacity-30">
              <svg className="w-12 h-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
              <p>System listening for farmer input signals...</p>
            </div>
          ) : (
            <>
              {events.map((event) => (
                <div key={event.event_id} className={`flex gap-4 border-l-2 pl-4 py-1.5 transition-all duration-300 animate-in slide-in-from-left-2 ${eventColor(event)}`}>
                  <span className="text-zinc-600 shrink-0 select-none">[{new Date(event.created_at).toLocaleTimeString()}]</span>
                  <div className="flex-1">
                    <span className={eventTextColor(event)}>
                      {event.message || event.event_type}
                    </span>
                    {event.step && (
                      <span className="ml-2 text-zinc-600 text-[9px]">({event.step})</span>
                    )}
                  </div>
                </div>
              ))}
              {events.length === 0 && job.status === 'running' && (
                <div className="flex items-center gap-2 text-zinc-500">
                  <div className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse" />
                  Processing... waiting for events
                </div>
              )}
              {job.status === 'completed' && job.result && (
                <div className="mt-4 space-y-3">
                  {/* AEGIS Context Mode */}
                  <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                    <div className="flex items-center justify-between">
                      <div className="text-[9px] font-bold text-zinc-600 uppercase">AEGIS Context</div>
                      <span className={`text-[9px] font-bold font-mono ${
                        job.result.use_aegis_context === false ? 'text-zinc-400' : 'text-emerald-400'
                      }`}>
                        {job.result.use_aegis_context === false ? 'OFF' : 'ON'}
                      </span>
                    </div>
                    {job.result.aegis_context?.aegis_available && (
                      <div className="mt-2 text-[10px] text-zinc-400 space-y-1">
                        <div>Risk Level: <span className="text-amber-400">{job.result.aegis_context.risk_level || 'UNKNOWN'}</span></div>
                        <div>Scan ID: <span className="text-zinc-300">{job.result.aegis_context.scan_id || '—'}</span></div>
                      </div>
                    )}
                    {job.result.aegis_context?.disabled && (
                      <p className="mt-2 text-[10px] text-zinc-500">Run executed without AEGIS enrichment.</p>
                    )}
                    {!job.result.aegis_context && (
                      <p className="mt-2 text-[10px] text-zinc-500">No AEGIS context metadata returned.</p>
                    )}
                  </div>

                  {/* Satellite Evidence (loan flow) */}
                  {(job.result.satellite_report || job.result.visualization_artifacts) && (() => {
                    const sat = (job.result.satellite_report || {}) as Record<string, any>;
                    const viz = (job.result.visualization_artifacts || {}) as Record<string, any>;
                    const ndviSeries = Array.isArray(sat.ndvi_series) ? sat.ndvi_series : [];

                    const validSeries = ndviSeries
                      .map((row: any, idx: number) => {
                        const raw = row?.ndvi_mean;
                        const n = typeof raw === 'number' ? raw : Number(raw);
                        return Number.isFinite(n) ? { idx, value: n, month: String(row?.month || '') } : null;
                      })
                      .filter(Boolean) as Array<{ idx: number; value: number; month: string }>;

                    const chartWidth = 420;
                    const chartHeight = 120;
                    const chartPad = 12;
                    const minY = 0;
                    const maxY = 1;
                    const toX = (i: number, total: number) =>
                      total <= 1
                        ? chartPad
                        : chartPad + ((chartWidth - chartPad * 2) * i) / (total - 1);
                    const toY = (v: number) => {
                      const clamped = Math.max(minY, Math.min(maxY, v));
                      return chartHeight - chartPad - ((chartHeight - chartPad * 2) * (clamped - minY)) / (maxY - minY);
                    };
                    const polyline = validSeries
                      .map((p, i) => `${toX(i, validSeries.length)},${toY(p.value)}`)
                      .join(' ');

                    return (
                      <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-xl space-y-3">
                        <div className="text-[10px] font-bold text-cyan-400 uppercase">Satellite Evidence (Earth Engine)</div>
                        <div className="grid grid-cols-2 gap-2 text-[10px] text-zinc-400">
                          <div>Current NDVI: <span className="text-zinc-200">{sat.ndvi != null ? Number(sat.ndvi).toFixed(2) : '—'}</span></div>
                          <div>NDVI Trend: <span className="text-zinc-200">{sat.ndvi_trend != null ? Number(sat.ndvi_trend).toFixed(2) : '—'}</span></div>
                          <div>Rainfall 30d: <span className="text-zinc-200">{sat.rainfall_30d != null ? `${Number(sat.rainfall_30d).toFixed(1)} mm` : '—'}</span></div>
                          <div>Field Snapped: <span className="text-zinc-200">{sat.field_snapped ? 'Yes' : 'No'}</span></div>
                        </div>

                        {(viz.rgb_thumb_url || viz.ndvi_thumb_url) && (
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div>
                              <div className="text-[9px] text-zinc-500 mb-1 uppercase">RGB Thumbnail</div>
                              {viz.rgb_thumb_url ? (
                                <img src={viz.rgb_thumb_url} alt="EE RGB thumbnail" className="w-full h-32 object-cover rounded border border-zinc-800" />
                              ) : (
                                <div className="w-full h-32 rounded border border-zinc-800 bg-zinc-950 flex items-center justify-center text-[10px] text-zinc-600">Not available</div>
                              )}
                            </div>
                            <div>
                              <div className="text-[9px] text-zinc-500 mb-1 uppercase">NDVI Thumbnail</div>
                              {viz.ndvi_thumb_url ? (
                                <img src={viz.ndvi_thumb_url} alt="EE NDVI thumbnail" className="w-full h-32 object-cover rounded border border-zinc-800" />
                              ) : (
                                <div className="w-full h-32 rounded border border-zinc-800 bg-zinc-950 flex items-center justify-center text-[10px] text-zinc-600">Not available</div>
                              )}
                            </div>
                          </div>
                        )}

                        <div>
                          <div className="text-[9px] text-zinc-500 mb-1 uppercase">NDVI Time Series (12 months)</div>
                          {validSeries.length >= 2 ? (
                            <div className="rounded border border-zinc-800 bg-zinc-950/60 p-2">
                              <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} className="w-full h-28">
                                <line x1={chartPad} y1={toY(0.2)} x2={chartWidth - chartPad} y2={toY(0.2)} stroke="#3f3f46" strokeWidth="1" />
                                <line x1={chartPad} y1={toY(0.5)} x2={chartWidth - chartPad} y2={toY(0.5)} stroke="#3f3f46" strokeWidth="1" />
                                <line x1={chartPad} y1={toY(0.8)} x2={chartWidth - chartPad} y2={toY(0.8)} stroke="#3f3f46" strokeWidth="1" />
                                <polyline fill="none" stroke="#22d3ee" strokeWidth="2" points={polyline} />
                                {validSeries.map((p, i) => (
                                  <circle key={`${p.month}-${i}`} cx={toX(i, validSeries.length)} cy={toY(p.value)} r="2.5" fill="#34d399" />
                                ))}
                              </svg>
                              <div className="flex justify-between text-[9px] text-zinc-500 mt-1">
                                <span>{validSeries[0]?.month || 'start'}</span>
                                <span>{validSeries[validSeries.length - 1]?.month || 'latest'}</span>
                              </div>
                            </div>
                          ) : (
                            <div className="text-[10px] text-zinc-600">NDVI series unavailable for this run.</div>
                          )}
                        </div>
                      </div>
                    );
                  })()}

                  {/* SMS to Farmer */}
                  <div className="p-4 bg-emerald-500/5 border border-emerald-500/20 rounded-xl">
                    <div className="text-[10px] font-bold text-emerald-500 uppercase mb-2">SMS to Farmer</div>
                    <p className="text-zinc-200 text-sm leading-relaxed">{job.result.farmer_response || job.result.sms_text || 'No response generated.'}</p>
                  </div>

                  {/* Decision + Intent row */}
                  <div className="grid grid-cols-2 gap-2">
                    {job.result.intent && (
                      <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                        <div className="text-[9px] font-bold text-zinc-600 uppercase mb-1">Intent</div>
                        <span className="text-xs font-mono text-blue-400">{job.result.intent}</span>
                      </div>
                    )}
                    {job.result.final_decision && (
                      <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                        <div className="text-[9px] font-bold text-zinc-600 uppercase mb-1">Decision</div>
                        <span className={`text-xs font-mono font-bold ${
                          job.result.final_decision === 'APPROVE_SMALL' ? 'text-emerald-400' :
                          job.result.final_decision === 'HOLD_FOR_VERIFICATION' ? 'text-orange-400' :
                          job.result.final_decision === 'DENY' ? 'text-red-400' : 'text-zinc-300'
                        }`}>{job.result.final_decision}</span>
                      </div>
                    )}
                  </div>

                  {/* Reasoning */}
                  {job.result.analysis_summary && Array.isArray(job.result.analysis_summary) && job.result.analysis_summary.length > 0 && (
                    <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                      <div className="text-[9px] font-bold text-zinc-600 uppercase mb-2">Why</div>
                      <ul className="space-y-1">
                        {job.result.analysis_summary.map((r: string, i: number) => (
                          <li key={i} className="text-[11px] text-zinc-400 flex gap-2">
                            <span className="text-zinc-600 shrink-0">•</span>
                            <span>{r}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Risk Flags */}
                  {job.result.risk_flags && Array.isArray(job.result.risk_flags) && job.result.risk_flags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {job.result.risk_flags.map((flag: string, i: number) => (
                        <span key={i} className="px-2 py-0.5 text-[9px] font-bold font-mono bg-red-500/10 text-red-400 border border-red-500/20 rounded">{flag}</span>
                      ))}
                    </div>
                  )}

                  {/* Pending Question / Next Step */}
                  {job.result.pending_question && (
                    <div className="p-3 bg-orange-500/5 border border-orange-500/20 rounded-lg">
                      <div className="text-[9px] font-bold text-orange-500 uppercase mb-1">Pending Question</div>
                      <p className="text-xs text-orange-300">{job.result.pending_question}</p>
                    </div>
                  )}

                  {/* Language + Approved Amount footer */}
                  <div className="flex items-center gap-3 text-[10px] text-zinc-600">
                    {job.result.language && <span>Language: {job.result.language}</span>}
                    {job.result.approved_amount > 0 && (
                      <span className="text-emerald-500 font-bold">Approved: ₦{Number(job.result.approved_amount).toLocaleString()}</span>
                    )}
                  </div>
                </div>
              )}
              {job.status === 'failed' && job.result?.error && (
                <div className="mt-4 p-4 bg-red-500/5 border border-red-500/20 rounded-xl">
                  <div className="text-[10px] font-bold text-red-500 uppercase mb-2">Error</div>
                  <p className="text-red-300 text-xs">{job.result.error}</p>
                </div>
              )}
              <div ref={eventsEndRef} />
            </>
          )}
        </div>

        {job && job.status === 'running' && (
           <div className="px-6 pb-6 bg-zinc-950/20">
              <div className="flex justify-between text-[10px] text-zinc-500 mb-2 font-mono">
                <span>PROCESSING</span>
                <span>{events.length} events</span>
              </div>
              <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
                <div className="h-full bg-emerald-500 animate-pulse rounded-full" style={{ width: '100%', opacity: 0.6 }} />
              </div>
           </div>
        )}
      </div>
    </div>
  );
};
