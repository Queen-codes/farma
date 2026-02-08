
import React, { useEffect, useState } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { apiService } from '../services/api';
import { AegisDashboardResponse } from '../types';

export const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<AegisDashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiService.getAegisDashboard()
      .then(setStats)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="p-8 text-red-400">Failed to load dashboard: {error}</div>;
  if (!stats) return <div className="p-8 text-zinc-500">Loading intelligence...</div>;

  const totalConflicts = stats.state_summaries.reduce((sum, s) => sum + s.conflict_events, 0);
  const totalDisplaced = stats.state_summaries.reduce((sum, s) => sum + (s.idp_estimate || 0), 0);
  const displStr = totalDisplaced >= 1_000_000
    ? `${(totalDisplaced / 1_000_000).toFixed(1)}M`
    : totalDisplaced >= 1_000
      ? `${(totalDisplaced / 1_000).toFixed(0)}K`
      : totalDisplaced.toString();

  const worstFood = stats.state_summaries.length > 0
    ? stats.state_summaries.reduce((worst, s) => {
        const levels = ['minimal', 'stressed', 'crisis', 'emergency', 'famine'];
        const rank = levels.indexOf(s.food_insecurity_level?.toLowerCase() || '');
        const worstRank = levels.indexOf(worst.toLowerCase());
        return rank > worstRank ? s.food_insecurity_level : worst;
      }, 'Minimal')
    : 'N/A';

  const priorityStates = stats.state_summaries
    .filter(s => s.priority_level === 'HIGH' || s.priority_level === 'CRITICAL')
    .sort((a, b) => (b.priority_score || 0) - (a.priority_score || 0))
    .slice(0, 5);

  const chartData = stats.state_summaries.map(s => ({
    name: s.state_name.slice(0, 4),
    conflict: s.conflict_events,
    displaced: (s.idp_estimate || 0) / 1000,
  }));

  return (
    <div className="p-6 space-y-6 animate-in fade-in duration-500">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Intelligence Overview</h1>
          <p className="text-zinc-400 text-sm">Real-time humanitarian monitoring across Northern Nigeria</p>
        </div>
        <div className="flex gap-4">
          <div className="px-4 py-2 bg-zinc-900 border border-zinc-800 rounded-lg text-xs font-mono">
            <span className="text-zinc-500 uppercase">Latest Scan:</span>{' '}
            <span className="text-emerald-500">{stats.latest_scan?.run_id || 'None'}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: 'Conflict Events', val: totalConflicts, delta: `${stats.state_summaries.length} states`, color: 'text-red-500' },
          { label: 'Total Displaced', val: displStr, delta: 'Estimated', color: 'text-orange-500' },
          { label: 'Food Security', val: worstFood, delta: 'Worst level', color: 'text-yellow-500' },
          { label: 'Reports Generated', val: stats.total_reports, delta: `${stats.total_scans} scans`, color: 'text-blue-500' },
        ].map((item, i) => (
          <div key={i} className="bg-zinc-900/50 border border-zinc-800 p-5 rounded-xl">
            <p className="text-zinc-500 text-xs font-medium uppercase tracking-wider">{item.label}</p>
            <div className="flex items-baseline gap-2 mt-2">
              <h2 className="text-2xl font-bold text-white">{item.val}</h2>
              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded bg-zinc-800 ${item.color}`}>{item.delta}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-zinc-900/30 border border-zinc-800 rounded-2xl p-6">
          <h3 className="text-sm font-semibold mb-6">Conflict Events by State</h3>
          <div className="h-[300px] w-full">
            {chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="colorConflict" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                  <XAxis dataKey="name" stroke="#52525b" fontSize={12} tickLine={false} axisLine={false} />
                  <YAxis stroke="#52525b" fontSize={12} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#18181b', border: '1px solid #3f3f46', borderRadius: '8px' }}
                    itemStyle={{ fontSize: '12px' }}
                  />
                  <Area type="monotone" dataKey="conflict" stroke="#ef4444" fillOpacity={1} fill="url(#colorConflict)" name="Conflicts" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-zinc-600 text-sm">
                No scan data available. Run an AEGIS scan first.
              </div>
            )}
          </div>
        </div>

        <div className="bg-zinc-900/30 border border-zinc-800 rounded-2xl p-6">
          <h3 className="text-sm font-semibold mb-6">Top Priority States</h3>
          <div className="space-y-4">
            {priorityStates.length > 0 ? (
              priorityStates.map((state, i) => (
                <div key={i} className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-lg border border-zinc-700/50">
                  <div className="flex items-center gap-3">
                    <div className={`w-2 h-2 rounded-full ${state.priority_level === 'CRITICAL' ? 'bg-red-500' : 'bg-orange-500'} animate-pulse`} />
                    <div>
                      <span className="text-sm font-medium">{state.state_name}</span>
                      <div className="text-[10px] text-zinc-500">
                        IPC {state.ipc_phase || '?'} | {state.food_insecurity_level}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-bold text-red-400">{state.conflict_events} events</div>
                    <div className="text-[10px] text-zinc-500">Score: {state.priority_score || '—'}</div>
                  </div>
                </div>
              ))
            ) : (
              stats.focus_states.map((state, i) => (
                <div key={i} className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-lg border border-zinc-700/50">
                  <div className="flex items-center gap-3">
                    <div className="w-2 h-2 rounded-full bg-zinc-500" />
                    <span className="text-sm font-medium">{state}</span>
                  </div>
                  <span className="text-[10px] text-zinc-500">No scan data</span>
                </div>
              ))
            )}
            <div className="pt-4 border-t border-zinc-800">
               <button className="w-full py-2 text-xs font-bold text-emerald-500 hover:bg-emerald-500/10 rounded-lg transition-colors">
                 RUN NEW AEGIS SCAN
               </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
