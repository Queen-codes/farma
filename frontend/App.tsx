
import React, { useState } from 'react';
import { Dashboard } from './components/Dashboard';
import { FarmerSimulation } from './components/FarmerSimulation';
import { AegisPipeline } from './components/AegisPipeline';

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'farmer' | 'aegis'>('dashboard');

  return (
    <div className="flex flex-col h-screen bg-black overflow-hidden font-sans">
      {/* Top Header */}
      <header className="h-16 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-md flex items-center justify-between px-6 shrink-0 z-40">
        <div className="flex items-center gap-4">
          <div className="w-8 h-8 bg-emerald-600 rounded-lg flex items-center justify-center font-bold text-white shadow-lg shadow-emerald-900/40">
            F
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight text-white leading-tight">FARMA</h1>
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Nigeria Intelligence</p>
          </div>
        </div>

        <nav className="hidden md:flex bg-zinc-900/50 p-1 rounded-xl border border-zinc-800">
          {[
            { id: 'dashboard', label: 'Overview' },
            { id: 'farmer', label: 'Farmer Simulation' },
            { id: 'aegis', label: 'AEGIS Pipeline' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === tab.id 
                  ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-900/20' 
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-zinc-700 to-zinc-600 border border-zinc-600" />
        </div>
      </header>

      {/* Main Layout Body */}
      <main className="flex-1 overflow-hidden flex">
        <div className="flex-1 overflow-y-auto bg-black">
          {activeTab === 'dashboard' && <Dashboard />}
          {activeTab === 'farmer' && <FarmerSimulation />}
          {activeTab === 'aegis' && <AegisPipeline />}
        </div>
      </main>

      {/* Footer / Status bar */}
      <footer className="h-8 border-t border-zinc-900 bg-zinc-950 px-6 flex items-center justify-between text-[9px] font-mono text-zinc-600 shrink-0">
        <div className="flex gap-4">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full shadow-[0_0_8px_rgba(16,185,129,0.5)]" />
            SYSTEM ONLINE
          </span>
          <span>LAT: 9.0820° N | LON: 8.6753° E (ABUJA)</span>
        </div>
        <div className="flex gap-4">
          <span className="text-zinc-400">GOOGLE GEMINI HACKATHON 2025/2026</span>
        </div>
      </footer>
    </div>
  );
};

export default App;
