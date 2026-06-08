import React from 'react';
import { ServerLauncher } from './ServerLauncher/ServerLauncher';

export const SetupTab: React.FC<{ server: any }> = ({ server }) => {
  return (
    <div className="p-6 h-full flex flex-col">
      <div className="max-w-3xl w-full mx-auto flex-1 flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold mb-2 text-sky-400">Extus Viewer Setup</h2>
          <p className="text-slate-400 text-sm">
            Extus is a lightweight, decoupled 3D viewer. It streams the latest authenticated STL artifact for your active project, so you get instant visual feedback anytime Intus compiles a new CAD model.
          </p>
        </div>
        
        <ServerLauncher server={server} title="File Server" accentColor="bg-sky-600 hover:bg-sky-500" />
      </div>
    </div>
  );
};
