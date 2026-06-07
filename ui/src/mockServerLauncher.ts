export function useServerLauncher(config: any) {
  const workflowBase = config.serverName.split('-')[0];
  const baseUrl = import.meta.env?.VITE_API_URL || 'http://localhost:8000';
  const serverUrl = `${baseUrl}/api/${workflowBase}`;
  
  return {
    pythonInstalled: true, installingPython: false, availableVenvs: ['docker'], selectedVenv: 'docker', port: 8000, portFree: false,
    packages: [], depsStatus: {}, checkingDeps: false, installingDeps: false, allDepsInstalled: true,
    serverRunning: true, connected: true, connecting: false, connectionError: null, serverUrl, autoStart: true, logs: ['Connected to Docker Backend'], creatingVenv: false,
    gpuInfo: null, modelsPath: null, downloadProgress: null, needsFfmpeg: false, ffmpegInstalled: true, installingFfmpeg: false,
    setSelectedVenv: () => {}, setPort: () => {}, setAutoStart: () => {}, installPython: async () => {}, installDeps: async () => {}, installFfmpeg: async () => {},
    startServer: async () => {}, stopServer: async () => {}, createVenv: async () => {}, addLog: () => {},
  };
}
