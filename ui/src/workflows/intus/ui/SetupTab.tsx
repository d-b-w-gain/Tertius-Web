import React from 'react';
import { ServerLauncher } from './ServerLauncher/ServerLauncher';

export const SetupTab: React.FC<{ server: any }> = ({ server }) => {
  return (
    <div className="p-6 h-full flex flex-col">
      <div className="max-w-3xl w-full mx-auto flex-1 flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold mb-2 text-indigo-400">Intus Engine Setup</h2>
          <p className="text-slate-400 text-sm">
            Intus acts as the core CAD compiler. It runs `build123d` natively to generate true 3D models and manages your projects.
          </p>
        </div>
        
        <ServerLauncher server={server} />
      </div>
    </div>
  );
};
