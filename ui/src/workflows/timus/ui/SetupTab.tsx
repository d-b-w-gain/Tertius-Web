import React from 'react';
import { ServerLauncher } from './ServerLauncher/ServerLauncher';
import type { ServerHandle } from './ServerLauncher/useServerLauncher';

export const SetupTab: React.FC<{ server: ServerHandle }> = ({ server }) => {
  return (
    <div className="p-6 h-full flex flex-col overflow-y-auto min-h-0">
      <div className="max-w-3xl w-full mx-auto flex-1 flex flex-col gap-6 min-h-0">
        <div>
          <h2 className="text-xl font-bold mb-2 text-indigo-400">Timus Drafting Engine Setup</h2>
          <p className="text-slate-400 text-sm">
            Timus acts as the 2D CAD drafting board. It projects your 3D models into SVGs using Hidden Line Removal (HLR).
          </p>
        </div>
        
        <ServerLauncher server={server} />
      </div>
    </div>
  );
};
