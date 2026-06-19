import React from 'react';
import { ServerLauncher } from './ServerLauncher/ServerLauncher';
import type { ServerHandle } from './ServerLauncher/useServerLauncher';

export const SetupTab: React.FC<{ server: ServerHandle }> = ({ server }) => {
  return (
    <div className="p-6 h-full flex flex-col">
      <div className="max-w-3xl w-full mx-auto flex-1 flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold mb-2 text-emerald-400">Artus AI Setup</h2>
          <p className="text-slate-400 text-sm">
            Artus is the AI-driven Feature Tree Editor. It parses the active Intus project to extract parameters, and uses an LLM to rewrite the CAD logic on the fly.
          </p>
        </div>
        
        <ServerLauncher server={server} title="AI Server" accentColor="bg-emerald-600 hover:bg-emerald-500" />
      </div>
    </div>
  );
};
