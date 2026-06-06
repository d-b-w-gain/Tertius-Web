import React from 'react';
import { useServerLauncher } from './ui/ServerLauncher/useServerLauncher';
import { CompilerTab } from './ui/CompilerTab';

export const IntusWindow: React.FC<{ isActive?: boolean }> = ({ isActive = true }) => {
  const server = useServerLauncher({
    workflowFolder: 'tertius/intus',
    scriptName: 'intus_server.py',
    port: 8891,
    serverName: 'intus-compiler',
    packages: ['fastapi', 'uvicorn[standard]', 'build123d'],
  });

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 font-sans">
      <CompilerTab serverUrl={server.serverUrl} isActive={isActive} />
    </div>
  );
};
