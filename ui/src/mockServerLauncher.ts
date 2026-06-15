import { resolveWorkflowServerUrl } from './workflows/shared/apiConfig'

type LauncherConfig = {
  serverName: string;
  [key: string]: unknown;
};

type DependencyStatus = {
  installed?: boolean;
  importValid?: boolean;
  importError?: string;
};

export interface ServerHandle {
  pythonInstalled: boolean | null;
  installingPython: boolean;
  availableVenvs: string[];
  selectedVenv: string;
  port: number;
  portFree: boolean | null;
  packages: string[];
  depsStatus: Record<string, DependencyStatus>;
  checkingDeps: boolean;
  installingDeps: boolean;
  allDepsInstalled: boolean;
  serverRunning: boolean;
  connected: boolean;
  connecting: boolean;
  connectionError: string | null;
  serverUrl: string;
  autoStart: boolean;
  logs: string[];
  creatingVenv: boolean;
  gpuInfo: { type: 'cuda' | 'mps' | 'cpu'; name?: string; cudaVersion?: string } | null;
  modelsPath: string | null;
  downloadProgress: { sizeFormatted: string } | null;
  needsFfmpeg: boolean;
  ffmpegInstalled: boolean;
  installingFfmpeg: boolean;
  setSelectedVenv: (value: string) => void;
  setPort: (value: number) => void;
  setAutoStart: (value: boolean) => void;
  installPython: () => Promise<void>;
  installDeps: () => Promise<void>;
  installFfmpeg: () => Promise<void>;
  startServer: () => Promise<void>;
  stopServer: () => Promise<void>;
  createVenv: (name: string) => Promise<void>;
  addLog: (message: string) => void;
}

export function useServerLauncher(config: LauncherConfig): ServerHandle {
  const workflowBase = config.serverName.split('-')[0] ?? '';
  const baseUrl = import.meta.env?.VITE_API_URL;
  const serverUrl = resolveWorkflowServerUrl(workflowBase, baseUrl);
  
  return {
    pythonInstalled: true, installingPython: false, availableVenvs: ['docker'], selectedVenv: 'docker', port: 8000, portFree: false,
    packages: [], depsStatus: {}, checkingDeps: false, installingDeps: false, allDepsInstalled: true,
    serverRunning: true, connected: true, connecting: false, connectionError: null, serverUrl, autoStart: true, logs: ['Connected to Docker Backend'], creatingVenv: false,
    gpuInfo: null, modelsPath: null, downloadProgress: null, needsFfmpeg: false, ffmpegInstalled: true, installingFfmpeg: false,
    setSelectedVenv: () => {}, setPort: () => {}, setAutoStart: () => {}, installPython: async () => {}, installDeps: async () => {}, installFfmpeg: async () => {},
    startServer: async () => {}, stopServer: async () => {}, createVenv: async () => {}, addLog: () => {},
  };
}
