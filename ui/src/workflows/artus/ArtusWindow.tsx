import React from 'react';
import { useServerLauncher } from './ui/ServerLauncher/useServerLauncher';
import { FeatureTreeTab } from './ui/FeatureTreeTab';

export const ArtusWindow: React.FC<{ isActive?: boolean }> = ({ isActive = true }) => {
  const server = useServerLauncher({
    workflowFolder: 'tertius/artus',
    scriptName: 'artus_server.py',
    port: 8893,
    serverName: 'artus-ai',
    packages: ['fastapi', 'uvicorn[standard]', 'pydantic'],
  });

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 font-sans">
      <FeatureTreeTab serverUrl={server.serverUrl} isActive={isActive} />
    </div>
  );
};
