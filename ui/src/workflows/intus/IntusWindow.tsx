import React, { useState, useEffect } from 'react';
import { useServerLauncher } from './ui/ServerLauncher/useServerLauncher';
import { CompilerTab } from './ui/CompilerTab';
import { UsageTab } from './ui/UsageTab';
import { apiFetch } from '../../api/client';
import { useAuth } from '../../auth/AuthProvider';

export const IntusWindow: React.FC<{ isActive?: boolean }> = ({ isActive = true }) => {
  const server = useServerLauncher({
    workflowFolder: 'tertius/intus',
    scriptName: 'intus_server.py',
    port: 8891,
    serverName: 'intus-compiler',
    packages: ['fastapi', 'uvicorn[standard]', 'build123d'],
  });

  const { getAccessToken } = useAuth();
  const [activeSubTab, setActiveSubTab] = useState<'compiler' | 'usage'>('compiler');
  const [showUsageTab, setShowUsageTab] = useState<boolean>(false);
  const [checkedUsage, setCheckedUsage] = useState(false);

  useEffect(() => {
    if (!server.serverUrl || checkedUsage) return;
    setCheckedUsage(true);
    let cancelled = false;
    const check = async () => {
      try {
        const token = await getAccessToken();
        const res = await apiFetch(`${server.serverUrl}/usage/summary`, async () => token);
        if (!cancelled) {
          setShowUsageTab(res.ok);
        }
      } catch {
        if (!cancelled) setShowUsageTab(false);
      }
    };
    void check();
    return () => { cancelled = true; };
  }, [server.serverUrl, getAccessToken, checkedUsage]);

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 font-sans">
      {showUsageTab && (
        <div className="flex border-b border-slate-800 bg-slate-950 px-3">
          <button
            onClick={() => setActiveSubTab('compiler')}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeSubTab === 'compiler'
                ? 'border-indigo-500 text-indigo-300'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            Compiler
          </button>
          <button
            onClick={() => setActiveSubTab('usage')}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeSubTab === 'usage'
                ? 'border-indigo-500 text-indigo-300'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            Usage
          </button>
        </div>
      )}
      {activeSubTab === 'compiler' || !showUsageTab ? (
        <CompilerTab serverUrl={server.serverUrl} isActive={isActive && activeSubTab === 'compiler'} />
      ) : (
        <UsageTab serverUrl={server.serverUrl} getAccessToken={getAccessToken} />
      )}
    </div>
  );
};
