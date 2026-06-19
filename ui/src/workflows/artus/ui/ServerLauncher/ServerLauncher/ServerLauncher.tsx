import React, { useRef } from 'react';
// ============================================================
// ServerLauncher.tsx — Canonical v3.0.0
// Drop-in UI for Python server management.
// Pair with useServerLauncher.ts — no other files needed.
// ============================================================

import type { ServerHandle } from './useServerLauncher';

interface Props {
  server: ServerHandle;
  title?: string;
  accentColor?: string;
  launchLabel?: string;
}

export const ServerLauncher: React.FC<Props> = ({
  server: s,
  title = 'Server Setup',
  accentColor = 'bg-cyan-500 hover:bg-cyan-400',
  launchLabel = 'Start Server',
}) => {
  const Panel = ({ children }: { children: React.ReactNode }) => (
    <div className="flex-1 overflow-y-auto p-6 flex justify-center">
      <div className="w-full max-w-md space-y-4">
        <h2 className="text-cyan-400 text-lg font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  );

  // Checking Python...
  if (s.pythonInstalled === null) {
    return (
      <Panel>
        <Section>
          <div className="flex items-center gap-2 text-slate-400">
            <span className="animate-pulse">●</span>
            <span className="text-sm">Checking Python installation...</span>
          </div>
        </Section>
      </Panel>
    );
  }

  // Python not installed
  if (s.pythonInstalled === false) {
    return (
      <Panel>
        <div className="bg-[rgba(30,30,50,0.8)] rounded-lg p-4 border border-orange-500/50">
          <div className="text-orange-400 text-sm font-medium mb-2">Python Not Installed</div>
          <p className="text-slate-400 text-xs mb-3">Python 3.14 is required. Click below to install.</p>
          <button
            onClick={s.installPython}
            disabled={s.installingPython}
            className={`w-full border-none text-white p-3 rounded cursor-pointer text-sm font-medium ${
              s.installingPython ? 'bg-slate-700 cursor-wait' : 'bg-orange-500 hover:bg-orange-400'
            }`}
          >
            {s.installingPython ? 'Installing...' : 'Install Python 3.14'}
          </button>
        </div>
        <Logs logs={s.logs} />
      </Panel>
    );
  }

  return (
    <Panel>
      {/* Venv Selection */}
      <Section label="Python Venv">
        <select
          value={s.selectedVenv}
          onChange={e => s.setSelectedVenv(e.target.value)}
          disabled={s.serverRunning || s.creatingVenv}
          className="w-full bg-slate-700 border border-slate-600 text-white p-2 text-sm rounded disabled:opacity-50"
        >
          {s.availableVenvs.length === 0 && <option value="">No venvs available</option>}
          {s.availableVenvs.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </Section>

      {/* Create New Venv - Always visible */}
      <Section label="Create New Venv">
        <CreateVenvForm 
          onSubmit={(name) => s.createVenv(name)} 
          disabled={s.creatingVenv || s.serverRunning} 
          creating={s.creatingVenv}
        />
      </Section>

      {/* Port */}
      <Section label="Port">
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={s.port}
            onChange={e => s.setPort(parseInt(e.target.value) || s.port)}
            disabled={s.serverRunning}
            min={1024} max={65535}
            className="flex-1 bg-slate-700 border border-slate-600 text-white p-2 text-sm rounded text-center disabled:opacity-50"
          />
          <span className={`text-sm ${s.portFree === true ? 'text-green-400' : s.portFree === false ? 'text-red-400' : 'text-slate-500'}`}>
            {s.portFree === true ? '✓' : s.portFree === false ? '✗' : '?'}
          </span>
        </div>
      </Section>

      {/* GPU Info */}
      {s.gpuInfo && (
        <Section label="GPU">
          <div className={`flex items-center gap-2 text-sm ${
            s.gpuInfo.type === 'cuda' ? 'text-green-400' : s.gpuInfo.type === 'mps' ? 'text-blue-400' : 'text-slate-400'
          }`}>
            <span>{s.gpuInfo.type === 'cuda' ? '🎮' : s.gpuInfo.type === 'mps' ? '🍎' : '💻'}</span>
            <span>
              {s.gpuInfo.type === 'cuda' && `${s.gpuInfo.name || 'NVIDIA'} (CUDA ${s.gpuInfo.cudaVersion})`}
              {s.gpuInfo.type === 'mps' && 'Apple Silicon (MPS)'}
              {s.gpuInfo.type === 'cpu' && 'CPU only'}
            </span>
          </div>
        </Section>
      )}

      {/* FFmpeg - required for audio processing (pydub) */}
      {s.needsFfmpeg && s.ffmpegInstalled === false && (
        <Section label="FFmpeg (required for audio)">
          <div className="flex items-center justify-between">
            <span className="text-orange-400 text-sm">✗ Not installed</span>
            <button
              onClick={s.installFfmpeg}
              disabled={s.installingFfmpeg}
              className={`border-none text-white py-1.5 px-3 rounded text-xs font-medium ${
                s.installingFfmpeg ? 'bg-slate-700 cursor-wait' : 'bg-orange-500 hover:bg-orange-400 cursor-pointer'
              }`}
            >
              {s.installingFfmpeg ? 'Installing...' : 'Install FFmpeg'}
            </button>
          </div>
          <p className="text-slate-500 text-xs mt-2">Required for audio format conversion (webm/opus decoding)</p>
        </Section>
      )}
      
      {s.needsFfmpeg && s.ffmpegInstalled === true && (
        <Section label="FFmpeg">
          <div className="flex items-center gap-2 text-green-400 text-sm">
            <span>✓</span>
            <span>Installed</span>
          </div>
        </Section>
      )}

      {/* Auto-start */}
      <Section>
        <label className="flex items-center gap-3 cursor-pointer">
          <input type="checkbox" checked={s.autoStart} onChange={e => s.setAutoStart(e.target.checked)} className="w-4 h-4 accent-pink-400" />
          <span className="text-slate-400 text-sm font-medium">Auto-start when ready</span>
        </label>
      </Section>

      {/* Packages */}
      {s.selectedVenv && (
        <Section label={<span>Packages {s.checkingDeps && <span className="text-slate-500">(checking...)</span>}</span>}>
          <div className="flex justify-end mb-2">
            <button
              onClick={s.installDeps}
              disabled={s.installingDeps || s.packages.length === 0 || s.allDepsInstalled}
              className={`border-none text-white py-1 px-3 rounded text-xs ${
                (s.installingDeps || s.allDepsInstalled) ? 'bg-slate-700 cursor-default' : 'bg-cyan-600 hover:bg-cyan-500 cursor-pointer'
              }`}
            >
              {s.installingDeps ? 'Installing...' : 'Install All'}
            </button>
          </div>
          <div className="flex flex-col gap-1">
            {s.packages.length === 0 ? (
              <span className="text-xs text-slate-600">No packages specified</span>
            ) : s.packages.map(pkg => {
              const st = s.depsStatus[pkg];
              const ok = st?.installed && st?.importValid !== false;
              const hasError = st?.importValid === false;
              return (
                <div key={pkg} className={`flex items-center justify-between py-1.5 px-2 rounded border ${
                  ok ? 'bg-green-500/15 border-green-500/40' : hasError ? 'bg-orange-500/15 border-orange-500/40' : 'bg-red-500/15 border-red-500/40'
                }`}>
                  <div className="flex flex-col">
                    <span className={`text-xs ${ok ? 'text-green-300' : hasError ? 'text-orange-300' : 'text-red-300'}`}>{pkg}</span>
                    {st?.importError && <span className="text-xs text-orange-400/70 truncate max-w-[200px]">{st.importError}</span>}
                  </div>
                  <span className={`text-xs ${ok ? 'text-green-400' : hasError ? 'text-orange-400' : 'text-red-400'}`}>
                    {ok ? '✓' : hasError ? '⚠' : '✗'}
                  </span>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* Download Progress */}
      {s.installingDeps && s.downloadProgress && (
        <Section label="Downloads">
          <div className="flex items-center gap-2 text-sm">
            <span className="animate-pulse text-yellow-400">⬇</span>
            <span className="text-slate-300">Models: <span className="text-yellow-400 font-mono">{s.downloadProgress.sizeFormatted}</span></span>
          </div>
        </Section>
      )}

      {/* Start / Stop */}
      <div className="flex gap-2">
        {!s.serverRunning ? (
          <button
            onClick={s.startServer}
            disabled={s.connecting || !s.selectedVenv || !s.allDepsInstalled}
            className={`flex-1 border-none text-white p-3 rounded text-sm font-medium transition-colors ${
              s.connecting || !s.selectedVenv || !s.allDepsInstalled
                ? 'bg-slate-700 cursor-not-allowed opacity-50'
                : `${accentColor} cursor-pointer`
            }`}
          >
            {s.connecting ? 'Starting...' : launchLabel}
          </button>
        ) : (
          <button onClick={s.stopServer} className="flex-1 bg-red-600 border-none text-white p-3 rounded cursor-pointer text-sm font-medium hover:bg-red-500">
            Stop Server
          </button>
        )}
      </div>

      {/* Connection Error */}
      {s.connectionError && (
        <div className="bg-orange-500/20 border border-orange-500/50 rounded-lg p-3">
          <span className="text-orange-400 text-sm">⚠ {s.connectionError}</span>
        </div>
      )}

      {/* Logs */}
      <Logs logs={s.logs} />
    </Panel>
  );
};

// ── Sub-components ──────────────────────────────────────────

const Section: React.FC<{ label?: React.ReactNode; children: React.ReactNode }> = ({ label, children }) => (
  <div className="bg-[rgba(30,30,50,0.8)] rounded-lg p-4 border border-slate-700">
    {label && <label className="text-slate-400 block mb-2 text-sm font-medium">{label}</label>}
    {children}
  </div>
);

// Simple uncontrolled form - always visible
const CreateVenvForm: React.FC<{ 
  onSubmit: (name: string) => void; 
  disabled: boolean;
  creating: boolean;
}> = ({ onSubmit, disabled, creating }) => {
  const inputRef = useRef<HTMLInputElement>(null);
  
  const handleSubmit = () => { 
    const val = inputRef.current?.value?.trim();
    if (val) {
      onSubmit(val);
      if (inputRef.current) inputRef.current.value = '';
    }
  };
  
  if (creating) {
    return (
      <div className="flex items-center gap-2 text-cyan-400 text-sm py-2">
        <span className="animate-pulse">●</span>
        <span>Creating venv...</span>
      </div>
    );
  }
  
  return (
    <div className="flex gap-2">
      <input
        ref={inputRef}
        type="text"
        defaultValue=""
        onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); }}
        placeholder="Enter name..."
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
        className="flex-1 bg-slate-700 border border-slate-600 text-white p-2 text-sm rounded disabled:opacity-50"
      />
      <button
        onClick={handleSubmit}
        disabled={disabled}
        className={`border-none text-white py-2 px-4 text-sm rounded font-medium ${
          disabled ? 'bg-slate-700 cursor-not-allowed opacity-50' : 'bg-cyan-600 hover:bg-cyan-500 cursor-pointer'
        }`}
      >
        Create
      </button>
    </div>
  );
};

const Logs: React.FC<{ logs: string[] }> = ({ logs }) => (
  <div className="bg-[rgba(30,30,50,0.8)] rounded-lg p-4 border border-slate-700">
    <h3 className="text-cyan-400 mb-3 text-sm font-medium">Logs</h3>
    <div className="bg-slate-950 p-2 rounded max-h-[150px] overflow-y-auto text-xs font-mono">
      {logs.length === 0 ? <div className="text-slate-600">No logs yet</div>
        : logs.map((log, i) => <div key={i} className={log.includes('✗') || log.includes('ERROR') ? 'text-red-400' : log.includes('✓') ? 'text-green-400' : 'text-slate-500'}>{log}</div>)}
    </div>
  </div>
);
