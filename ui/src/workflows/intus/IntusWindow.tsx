import React, { useState } from 'react';
import { useServerLauncher } from './ui/ServerLauncher/useServerLauncher';
import { SetupTab } from './ui/SetupTab';
import { CompilerTab } from './ui/CompilerTab';

type Tab = 'setup' | 'compiler';

export const IntusWindow: React.FC = () => {
  const server = useServerLauncher({
    workflowFolder: 'tertius/intus',
    scriptName: 'intus_server.py',
    port: 8891,
    serverName: 'intus-compiler',
    packages: ['fastapi', 'uvicorn[standard]', 'build123d'],
  });

  const [activeTab, setActiveTab] = useState<Tab>('setup');

  // Switch to compiler tab automatically once connected (if we were on setup)
  React.useEffect(() => {
    if (server.connected && activeTab === 'setup') {
      setActiveTab('compiler');
    }
  }, [server.connected]);

  const tabBtn = (active: boolean) =>
    `px-5 py-3 text-sm font-medium transition-all border-b-2 bg-transparent outline-none cursor-pointer ${
      active
        ? 'text-indigo-400 border-indigo-400 font-semibold'
        : 'text-slate-400 border-transparent hover:text-slate-200'
    }`;

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 selection:bg-indigo-500/30 font-sans">
      {/* Top Header & Navigation */}
      <div className="flex border-b border-slate-800 bg-slate-900/50 px-4 items-center">
        <div className="flex items-center gap-3 py-3 pr-6 border-r border-slate-850">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold shadow-lg bg-gradient-to-br from-indigo-500 to-purple-600 text-slate-950">
            ⚙️
          </div>
          <h1 className="text-sm font-bold tracking-tight">Intus Compiler</h1>
        </div>

        <button onClick={() => setActiveTab('setup')} className={tabBtn(activeTab === 'setup')}>
          🔌 Setup
        </button>

        <button 
          onClick={() => server.connected && setActiveTab('compiler')} 
          className={tabBtn(activeTab === 'compiler')} 
          disabled={!server.connected}
        >
          💻 Project & Compile
        </button>

        <div className="flex-1" />

        <div className="flex items-center gap-2 px-2 text-xs font-mono">
          <span className={`inline-block w-2.5 h-2.5 rounded-full ${server.connected ? 'bg-emerald-500 animate-pulse' : 'bg-slate-600'}`} />
          <span className={server.connected ? 'text-emerald-400 font-semibold' : 'text-slate-500'}>
            {server.connected ? 'ENGINE ONLINE' : 'ENGINE OFFLINE'}
          </span>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {activeTab === 'setup' && <SetupTab server={server} />}
        {activeTab === 'compiler' && <CompilerTab serverUrl={server.serverUrl} />}
      </div>
    </div>
  );
};
