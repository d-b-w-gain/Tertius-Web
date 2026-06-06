import React from 'react';
import { useServerLauncher } from './ui/ServerLauncher/useServerLauncher';
import { DraftingTab } from './ui/DraftingTab';

export const TimusWindow: React.FC<{ isActive?: boolean }> = ({ isActive = true }) => {
  const server = useServerLauncher({
    workflowFolder: 'tertius/timus',
    scriptName: 'timus_server.py',
    port: 8893,
    serverName: 'timus-drafting',
    packages: ['fastapi', 'uvicorn[standard]', 'build123d'],
  });

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 font-sans">
      <DraftingTab serverUrl={server.serverUrl} isActive={isActive} />
    </div>
  );
};
