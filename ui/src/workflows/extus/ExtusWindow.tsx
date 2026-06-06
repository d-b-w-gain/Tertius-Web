import React from 'react';
import { useServerLauncher } from './ui/ServerLauncher/useServerLauncher';
import { ViewerTab } from './ui/ViewerTab';

export const ExtusWindow: React.FC<{ isActive?: boolean }> = ({ isActive = true }) => {
  const server = useServerLauncher({
    workflowFolder: 'tertius/extus',
    scriptName: 'extus_server.py',
    port: 8892,
    serverName: 'extus-viewer',
    packages: ['fastapi', 'uvicorn[standard]'],
  });

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 font-sans">
      <ViewerTab serverUrl={server.serverUrl} isActive={isActive} />
    </div>
  );
};
