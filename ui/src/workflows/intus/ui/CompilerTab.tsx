import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import Editor from '@monaco-editor/react';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import { ACTIVE_PROJECT_CHANGED_EVENT, ProjectSelector } from '../../shared/ui/ProjectSelector';
import { createProjectStorage, type ProjectFileMetadata } from '../../shared/projectStorage';
import { GUEST_WORKSPACE_CHANGED_EVENT } from '../../shared/guestWorkspace';
import {
  ACTIVE_PROJECT_POLL_INTERVAL_MS,
  FILE_STATUS_POLL_INTERVAL_MS,
  getPollingDelay,
  shouldRunPollingRequest,
} from '../../shared/polling';

const COMPILE_AUTH_TIMEOUT_MS = 15_000;
const COMPILE_CREATE_JOB_TIMEOUT_MS = 20_000;
const COMPILE_STATUS_INITIAL_DELAY_MS = 1_000;
const COMPILE_STATUS_POLL_MS = 2_000;
const COMPILE_STATUS_RETRY_MS = 3_000;
const AI_EDIT_FILE_LIMIT = 20;

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  let timeoutId: number | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
  });

  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timeoutId !== undefined) {
      window.clearTimeout(timeoutId);
    }
  }
}

export const CompilerTab: React.FC<{ serverUrl: string, isActive?: boolean }> = ({ serverUrl, isActive = true }) => {
  const { authMode, getAccessToken } = useAuth();
  const isGuest = authMode === 'guest';
  const storage = useMemo(
    () => createProjectStorage({ authMode, serverUrl, getAccessToken }),
    [authMode, getAccessToken, serverUrl],
  );
  const [activeProject, setActiveProject] = useState<string>('');
  const [code, setCode] = useState<string>('');
  const [format, setFormat] = useState<string>('glb');
  const [quality, setQuality] = useState<string>('sketch');
  const [log, setLog] = useState<string>('');
  const [isCompiling, setIsCompiling] = useState(false);
  const [autoCompile, setAutoCompile] = useState<boolean>(true);
  const [failedCompileRetry, setFailedCompileRetry] = useState<{ code: string } | null>(null);
  
  const [files, setFiles] = useState<string[]>(['design.py']);
  const [activeFile, setActiveFile] = useState<string>('design.py');
  const [isCreatingFile, setIsCreatingFile] = useState(false);
  const [newFileName, setNewFileName] = useState('');
  const [fileMetadata, setFileMetadata] = useState<ProjectFileMetadata[]>([]);
  const [aiPrompt, setAiPrompt] = useState('');
  const [isApplyingAiEdit, setIsApplyingAiEdit] = useState(false);
  
  // Git UI State
  const [gitStatus, setGitStatus] = useState<{ is_git: boolean, commit?: string, history?: string[], label?: string }>({ is_git: false });
  const [activePane, setActivePane] = useState<'output' | 'history'>('output');
  
  const mtimeRef = useRef<number>(0);
  const isCompilingRef = useRef<boolean>(false);
  const loadRequestRef = useRef<number>(0);
  const pollTimerRef = useRef<number | undefined>(undefined);
  const compileRequestRef = useRef<number>(0);
  const startCompileRef = useRef<((nextCode: string, mode: 'manual' | 'auto') => Promise<void>) | null>(null);
  const activeProjectRef = useRef<string>('');
  const activeFileRef = useRef<string>('design.py');
  const codeRef = useRef<string>('');

  useEffect(() => {
    isCompilingRef.current = isCompiling;
  }, [isCompiling]);

  useEffect(() => {
    activeProjectRef.current = activeProject;
  }, [activeProject]);

  useEffect(() => {
    activeFileRef.current = activeFile;
  }, [activeFile]);

  useEffect(() => {
    codeRef.current = code;
  }, [code]);

  const setCompilingState = useCallback((value: boolean) => {
    isCompilingRef.current = value;
    setIsCompiling(value);
  }, []);

  const fetchGitStatus = useCallback(async (name: string) => {
    try {
      const data = await storage.getHistory(name);
      setGitStatus(data);
    } catch {
      setGitStatus({ is_git: false });
    }
  }, [storage]);

  const pollCompileJob = useCallback((projectName: string, jobId: string, requestId: number, mode: 'manual' | 'auto') => {
    const pollStartedAt = Date.now();
    const tick = async () => {
      if (compileRequestRef.current !== requestId) return;

      try {
        const res = await apiFetch(`${serverUrl}/projects/${projectName}/compile/jobs/${jobId}`, getAccessToken);
        const data = await res.json();

        if (!res.ok) {
          const message = data.user_message || data.short || data.error || 'Compile job status could not be loaded.';
          setLog(prev => `${prev}\n[ERROR] ${message}`);
          setFailedCompileRetry(data.retryable ? { code: codeRef.current } : null);
          if (mode === 'auto') setAutoCompile(false);
          setCompilingState(false);
          return;
        }

        if (data.status === 'succeeded') {
          const outputFormat = data.format || data.export_format || format;
          setLog(prev => `${prev}\n[SUCCESS] Compiled ${outputFormat} artifact ${data.artifact_id}`);
          setFailedCompileRetry(null);
          const postStatus = await storage.getStatus(projectName, activeFileRef.current);
          if (postStatus.mtime) {
            if (mtimeRef.current === 0 || postStatus.mtime <= mtimeRef.current) {
              mtimeRef.current = postStatus.mtime;
            } else {
              const latestCode = await storage.loadCode(projectName, activeFileRef.current);
              const shouldAutoCompileLatest = autoCompile && latestCode !== codeRef.current;
              setCode(latestCode);
              mtimeRef.current = postStatus.mtime;
              if (shouldAutoCompileLatest) {
                setCompilingState(false);
                void startCompileRef.current?.(latestCode, 'auto');
                return;
              }
            }
          }
          fetchGitStatus(projectName);
          if (mode === 'manual') setAutoCompile(true);
          setCompilingState(false);
          return;
        }

        if (data.status === 'failed') {
          const message = data.user_message || 'Compile failed. Try again.';
          const errorCode = data.error_code ? ` (${data.error_code})` : '';
          const details = data.error ? `\n${data.error}` : '';
          setLog(prev => `${prev}\n[ERROR]${errorCode} ${message}${details}`);
          setFailedCompileRetry(data.retryable ? { code: codeRef.current } : null);
          if (mode === 'auto') setAutoCompile(false);
          setCompilingState(false);
          return;
        }

        const createdAt = data.created_at ? Date.parse(data.created_at) : NaN;
        const elapsedFrom = Number.isNaN(createdAt) ? pollStartedAt : createdAt;
        const elapsed = formatElapsed(Date.now() - elapsedFrom);
        const statusText = data.status || 'running';
        setLog(`[INFO] Job ${jobId} is ${statusText}\n[INFO] Waiting ${elapsed} for ${projectName} ${data.format || format} compile...`);
        pollTimerRef.current = window.setTimeout(tick, COMPILE_STATUS_POLL_MS);
      } catch {
        const elapsed = formatElapsed(Date.now() - pollStartedAt);
        setLog(`[WARN] Waiting ${elapsed}; temporarily could not refresh compile job ${jobId}. Retrying...`);
        pollTimerRef.current = window.setTimeout(tick, COMPILE_STATUS_RETRY_MS);
      }
    };

    pollTimerRef.current = window.setTimeout(tick, COMPILE_STATUS_INITIAL_DELAY_MS);
  }, [autoCompile, fetchGitStatus, format, getAccessToken, serverUrl, setCompilingState, storage]);

  const startCompile = useCallback(async (nextCode: string, mode: 'manual' | 'auto') => {
    if (!activeProjectRef.current || isGuest || isCompilingRef.current) return;

    const projectName = activeProjectRef.current;
    const requestId = compileRequestRef.current + 1;
    compileRequestRef.current = requestId;
    if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current);

    setCompilingState(true);
    setFailedCompileRetry(null);
    setLog(mode === 'manual' ? `Compile queued for ${projectName}...` : 'External change detected. Compile queued...');

    try {
      const token = await withTimeout(
        getAccessToken(),
        COMPILE_AUTH_TIMEOUT_MS,
        'Compile could not start because authentication timed out. Please try again or sign in again.',
      );
      const res = await withTimeout(
        apiFetch(`${serverUrl}/projects/${projectName}/compile`, async () => token, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: nextCode, export_format: format, quality, file: activeFileRef.current })
        }),
        COMPILE_CREATE_JOB_TIMEOUT_MS,
        'Compile request timed out before a job was created. Please try again.',
      );
      const data = await res.json();

      if (!res.ok || !data.job_id) {
        setLog(`[ERROR] ${data.user_message || data.short || data.error || 'Failed to queue compile job'}`);
        setFailedCompileRetry(data.retryable ? { code: nextCode } : null);
        if (mode === 'auto') setAutoCompile(false);
        setCompilingState(false);
        return;
      }

      setLog(prev => `${prev}\n[INFO] Job ${data.job_id} is ${data.status || 'queued'}`);
      pollCompileJob(projectName, data.job_id, requestId, mode);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to reach server during compile.';
      setLog(`[ERROR] ${message}`);
      setFailedCompileRetry({ code: nextCode });
      if (mode === 'auto') setAutoCompile(false);
      setCompilingState(false);
    }
  }, [format, getAccessToken, isGuest, pollCompileJob, quality, serverUrl, setCompilingState]);

  useEffect(() => {
    startCompileRef.current = startCompile;
  }, [startCompile]);

  useEffect(() => () => {
    if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current);
  }, []);

  const loadProject = useCallback(async (
    projectName: string,
    preferredFile = activeFileRef.current,
    options: { saveCurrent?: boolean } = {},
  ) => {
    const requestId = loadRequestRef.current + 1;
    loadRequestRef.current = requestId;

    const currentProject = activeProjectRef.current;
    const currentFile = activeFileRef.current;
    const currentCode = codeRef.current;

    if (options.saveCurrent && currentProject && currentFile && currentProject !== projectName) {
      try {
        await storage.saveCode(currentProject, currentFile, currentCode);
      } catch (e) {
        setLog(prev => `${prev ? `${prev}\n` : ''}[WARN] Could not save ${currentProject}/${currentFile} before switching projects.`);
      }
    }

    setActiveProject(projectName);
    setCode('');
    mtimeRef.current = 0;
    fetchGitStatus(projectName);

    let metadata: ProjectFileMetadata[];
    try {
      metadata = await storage.listFileMetadata(projectName);
    } catch (e) {
      metadata = [];
    }
    if (loadRequestRef.current !== requestId) return;

    let nextFiles: string[];
    if (metadata.length > 0) {
      nextFiles = metadata.map(file => file.filename);
    } else {
      const projectFiles = await storage.listFiles(projectName);
      if (loadRequestRef.current !== requestId) return;
      nextFiles = projectFiles.length > 0 ? projectFiles : ['design.py'];
    }
    const nextFile = nextFiles.includes(preferredFile) ? preferredFile : nextFiles.includes('design.py') ? 'design.py' : nextFiles[0]!;
    const nextCode = await storage.loadCode(projectName, nextFile);
    if (loadRequestRef.current !== requestId) return;

    setFileMetadata(metadata);
    setFiles(nextFiles);
    setActiveFile(nextFile);
    setCode(nextCode);
  }, [fetchGitStatus, storage]);

  const refreshFileMetadata = useCallback(async (projectName: string) => {
    const metadata = await storage.listFileMetadata(projectName);
    setFileMetadata(metadata);
    setFiles(metadata.length > 0 ? metadata.map(file => file.filename) : ['design.py']);
    return metadata;
  }, [storage]);

  useEffect(() => {
    if (!isActive) return;
    let isMounted = true;
    const loadActiveProject = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const projectName = await storage.getActiveProject();
        if (!projectName || !isMounted || projectName === activeProject) {
          return;
        }

        await loadProject(projectName);
      } catch (e) {}
    };
    
    loadActiveProject();
    const interval = isGuest ? undefined : setInterval(loadActiveProject, getPollingDelay(ACTIVE_PROJECT_POLL_INTERVAL_MS));
    if (isGuest) {
      window.addEventListener(GUEST_WORKSPACE_CHANGED_EVENT, loadActiveProject);
    }
    return () => {
      isMounted = false;
      if (interval) clearInterval(interval);
      if (isGuest) {
        window.removeEventListener(GUEST_WORKSPACE_CHANGED_EVENT, loadActiveProject);
      }
    };
  }, [storage, activeProject, isGuest, isActive, loadProject]);

  useEffect(() => {
    if (isGuest && autoCompile) {
      setAutoCompile(false);
    }
  }, [autoCompile, isGuest]);

  useEffect(() => {
    if (isGuest) return;
    const handleImported = (event: Event) => {
      const detail = (event as CustomEvent<{ activeProject?: string; activeFile?: string }>).detail;
      if (!detail?.activeProject) return;

      void (async () => {
        await loadProject(detail.activeProject!, detail.activeFile || 'design.py');
      })();
    };
    window.addEventListener('tertius:guest-imported', handleImported);
    return () => window.removeEventListener('tertius:guest-imported', handleImported);
  }, [isGuest, storage, loadProject]);

  useEffect(() => {
    const handleActiveProjectChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ activeProject?: string }>).detail;
      if (!detail?.activeProject || detail.activeProject === activeProjectRef.current) return;
      void loadProject(detail.activeProject, 'design.py', { saveCurrent: !isGuest });
    };

    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChanged);
    return () => window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChanged);
  }, [isGuest, storage, loadProject]);

  useEffect(() => {
    if (!isGuest || !activeProject || !activeFile) return;
    const timeout = window.setTimeout(() => {
      void storage.saveCode(activeProject, activeFile, code);
    }, 500);
    return () => window.clearTimeout(timeout);
  }, [activeFile, activeProject, code, isGuest, storage]);

  // Poll for external file changes (e.g. from Artus)
  useEffect(() => {
    if (isGuest || !activeProject || !isActive) return;
    
    const checkSync = async () => {
      if (!shouldRunPollingRequest()) return;
      if (isCompilingRef.current) return;
      try {
        const data = await storage.getStatus(activeProject, activeFile);
        
        if (data.mtime) {
          if (mtimeRef.current === 0) {
             mtimeRef.current = data.mtime; // Initial baseline
          } else if (data.mtime > mtimeRef.current) {
             // File changed on disk! Sync it.
             mtimeRef.current = data.mtime;
             
             const newCode = await storage.loadCode(activeProject, activeFile);
             setCode(newCode);

             if (!autoCompile) {
               setLog(prev => prev + `\n[INFO] External change detected. Auto-compile is disabled.`);
               return;
             }
             
             void startCompile(newCode, 'auto');
          }
        }
      } catch (e) {
        // Silently fail if server is busy
      }
    };
    
    const interval = setInterval(checkSync, getPollingDelay(FILE_STATUS_POLL_INTERVAL_MS));
    return () => clearInterval(interval);
  }, [activeProject, isActive, activeFile, autoCompile, isGuest, storage, startCompile]);

  const switchFile = async (
    fileName: string,
    options: { saveCurrent?: boolean } = { saveCurrent: true },
  ) => {
    if (fileName === activeFile) return;
    
    if (options.saveCurrent !== false) {
      try {
        await storage.saveCode(activeProject, activeFile, code);
      } catch(e) {}
    }

    setActiveFile(fileName);
    mtimeRef.current = 0; // Prevent false-positive sync when switching files
    try {
      const nextCode = await storage.loadCode(activeProject, fileName);
      setCode(nextCode || '');
    } catch (e) {
      setLog(`Failed to load file: ${fileName}`);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploadedFiles = e.target.files;
    if (!uploadedFiles || uploadedFiles.length === 0) return;

    const newFileNames: string[] = [];

    for (let i = 0; i < uploadedFiles.length; i++) {
      const file = uploadedFiles.item(i);
      if (!file) continue;
      const text = await file.text();
      try {
        await storage.saveCode(activeProject, file.name, text);
        newFileNames.push(file.name);
      } catch (err) {
        alert(`Failed to upload ${file.name}`);
      }
    }

    if (newFileNames.length > 0) {
      setFiles(prev => Array.from(new Set([...prev, ...newFileNames])));
      if (newFileNames.includes('design.py')) {
        switchFile('design.py');
      } else {
        const firstFile = newFileNames[0];
        if (firstFile) {
          switchFile(firstFile);
        }
      }
    }

    e.target.value = '';
  };

  const handleNewFileSubmit = async () => {
    let name = newFileName.trim();
    if (!name) return;
    if (!name.endsWith('.py')) name += '.py';
    
    if (files.includes(name)) {
      alert("File already exists");
      return;
    }
    
    try {
      await storage.saveCode(activeProject, name, "");
      await refreshFileMetadata(activeProject);
      setIsCreatingFile(false);
      setNewFileName('');
      await switchFile(name);
    } catch (e) {
      alert("Network error creating file");
    }
  };

  const handleDeleteFile = async (fileName: string) => {
    if (fileName === 'design.py') return;
    if (!window.confirm(`Are you sure you want to delete ${fileName}?`)) return;
    
    try {
      await storage.deleteFile(activeProject, fileName);
      await refreshFileMetadata(activeProject);
      
      if (activeFile === fileName) {
        await switchFile('design.py', { saveCurrent: false });
      }
    } catch (e) {
      alert("Network error deleting file");
    }
  };

  const handleCodeChange = (value: string) => {
    setCode(value);
    if (isGuest && activeProject && activeFile) {
      void storage.saveCode(activeProject, activeFile, value);
    }
  };

  const handleCompile = async () => {
    if (isGuest) {
      setLog('Log in to compile and export this local draft.');
      return;
    }
    await startCompile(code, 'manual');
  };

  const hasEditableFilePointers = fileMetadata.length > 0 && fileMetadata.every(file => file.id && file.updated_at);

  const applyAiEdit = async () => {
    if (isGuest || !activeProject || !aiPrompt.trim() || !hasEditableFilePointers) return;
    setIsApplyingAiEdit(true);
    try {
      await storage.saveCode(activeProject, activeFile, code);
      const latestMetadata = await refreshFileMetadata(activeProject);
      const activeMetadata = latestMetadata.find(file => file.filename === activeFile);
      const remainingMetadata = latestMetadata.filter(file => file.filename !== activeFile);
      const requestFiles = [
        ...(activeMetadata ? [activeMetadata] : []),
        ...remainingMetadata,
      ]
        .filter(file => file.id && file.updated_at)
        .slice(0, AI_EDIT_FILE_LIMIT);
      if (requestFiles.length === 0) return;
      if (latestMetadata.length > AI_EDIT_FILE_LIMIT) {
        setLog(prev => `${prev ? `${prev}\n` : ''}[INFO] AI edit includes ${AI_EDIT_FILE_LIMIT} of ${latestMetadata.length} files.`);
      }
      const result = await storage.applyLlmFileEdit(activeProject, {
        prompt: aiPrompt.trim(),
        files: requestFiles.map(file => ({ id: file.id, filename: file.filename, updated_at: file.updated_at! })),
        active_file_id: activeMetadata?.id,
        metadata: { source: 'compiler_tab' },
      });
      setAiPrompt('');
      if (result.outcome === 'changed') {
        const nextMetadata = result.files.map(file => ({
          id: file.id,
          filename: file.filename,
          updated_at: file.updated_at,
        }));
        setFileMetadata(prev => prev.map(existing => nextMetadata.find(file => file.id === existing.id) || existing));
        setFiles(prev => Array.from(new Set([...prev, ...result.files.map(file => file.filename)])));
        const activeChanged = result.files.find(file => file.filename === activeFile) || result.files[0];
        if (activeChanged) {
          if (activeChanged.filename === activeFile) {
            // Staying on the current file: set code from the server response and
            // reset the polling baseline so the next poll establishes a fresh
            // mtime instead of treating the AI edit as a stale external change.
            mtimeRef.current = 0;
            setCode(activeChanged.content);
          } else {
            // Switching to a different changed file: route through switchFile so
            // the baseline reset and canonical server reload path are reused.
            // Avoid saving the current editor content (which would create an
            // extra snapshot) since the AI edit already persisted the changes.
            await switchFile(activeChanged.filename, { saveCurrent: false });
          }
        }
        setLog(prev => `${prev ? `${prev}\n` : ''}[INFO] AI updated ${result.files.length} file(s).${result.message ? ` ${result.message}` : ''}`);
        fetchGitStatus(activeProject);
      } else if (result.outcome === 'no_change') {
        setLog(prev => `${prev ? `${prev}\n` : ''}[INFO] ${result.message || 'AI did not need to change any files.'}`);
      } else {
        setLog(prev => `${prev ? `${prev}\n` : ''}[WARN] ${result.message || 'AI could not complete the requested edit.'}`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'AI file edit failed.';
      setLog(prev => `${prev ? `${prev}\n` : ''}[ERROR] ${message}`);
    } finally {
      setIsApplyingAiEdit(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-900 text-slate-200">
      {/* Toolbar */}
      <div className="flex items-center gap-4 p-3 border-b border-slate-800 bg-slate-950">
        
        {/* Project Name Display (Selector moved to Artus) */}
        <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-indigo-300">Project:</span>
            {isGuest ? (
              <ProjectSelector />
            ) : (
              <span className="px-3 py-1 bg-slate-800 border border-slate-700 rounded text-sm text-slate-300 font-mono">
                  {activeProject || 'Loading...'}
              </span>
            )}
        </div>

        <div className="flex-1" />

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">Quality:</span>
            <select 
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm focus:outline-none focus:border-indigo-500"
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
            >
              <option value="high">High</option>
              <option value="normal">Normal</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
              <option value="rough">Rough</option>
              <option value="sketch">Sketch</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">Export:</span>
            <select 
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm focus:outline-none focus:border-indigo-500"
              value={format}
              onChange={(e) => setFormat(e.target.value)}
            >
              <option value="stl">STL</option>
              <option value="step">STEP</option>
              <option value="glb">GLB (Binary GLTF)</option>
            </select>
          </div>
          <div className="flex items-center gap-2 ml-2 border-l border-slate-700 pl-4">
            <input 
              type="checkbox" 
              id="autoCompile" 
              checked={autoCompile} 
              disabled={isGuest}
              onChange={(e) => setAutoCompile(e.target.checked)} 
              className="rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-indigo-500"
            />
            <label htmlFor="autoCompile" className="text-xs text-slate-400 select-none cursor-pointer">Auto-compile</label>
          </div>
        </div>

        <button 
          onClick={handleCompile}
          disabled={isCompiling || isGuest}
          className={`px-4 py-1.5 rounded text-sm font-bold shadow-lg transition-all ${
            isCompiling || isGuest
              ? 'bg-indigo-600/50 text-indigo-200 cursor-not-allowed' 
              : 'bg-indigo-600 hover:bg-indigo-500 text-white'
          }`}
        >
          {isGuest ? 'Log in to compile' : isCompiling ? 'Compiling...' : '⚙️ Compile & Export'}
        </button>
      </div>

      {/* Editor & Console */}
      <div className="flex-1 flex min-h-0">
        <div className="w-2/3 border-r border-slate-800 flex flex-col">
          <div className="bg-slate-950 flex border-b border-slate-800 overflow-x-auto scrollbar-hide">
            {files.map(f => (
              <div 
                key={f}
                className={`flex items-center border-r border-slate-800 transition-colors ${
                  f === activeFile ? 'bg-slate-900 border-b-2 border-b-indigo-500' : 'hover:bg-slate-900'
                }`}
              >
                <button
                  onClick={() => switchFile(f)}
                  className={`px-4 py-2 text-xs font-mono ${f === activeFile ? 'text-indigo-300 font-bold' : 'text-slate-500 hover:text-slate-300'}`}
                >
                  {f}
                </button>
                {f !== 'design.py' && f === activeFile && (
                  <button 
                    onClick={(e) => { e.stopPropagation(); handleDeleteFile(f); }}
                    className="pr-2 pl-1 text-xs text-slate-600 hover:text-red-400"
                    title="Delete file"
                  >
                    ✕
                  </button>
                )}
              </div>
            ))}
            
            {isCreatingFile ? (
              <form onSubmit={(e) => { e.preventDefault(); handleNewFileSubmit(); }} className="flex items-center px-2 py-1">
                <input 
                  autoFocus
                  className="bg-slate-800 border border-slate-700 rounded px-2 py-0.5 text-xs focus:outline-none focus:border-indigo-500 w-32"
                  value={newFileName}
                  onChange={e => setNewFileName(e.target.value)}
                  placeholder="filename.py"
                  onBlur={() => setIsCreatingFile(false)}
                />
              </form>
            ) : (
              <div className="flex items-center">
                <button
                  onClick={() => setIsCreatingFile(true)}
                  className="px-3 py-2 text-xs font-mono text-slate-500 hover:text-slate-300 hover:bg-slate-900 transition-colors"
                  title="New File"
                >
                  +
                </button>
                <label
                  className="px-3 py-2 text-xs font-mono text-slate-500 hover:text-slate-300 hover:bg-slate-900 transition-colors cursor-pointer"
                  title="Upload Files"
                >
                  Upload
                  <input
                    type="file"
                    multiple
                    className="hidden"
                    onChange={handleFileUpload}
                  />
                </label>
              </div>
            )}

            {!isGuest && (
              <div className="flex-1" />
            )}

            {!isGuest && (
              <form
                onSubmit={(e) => { e.preventDefault(); void applyAiEdit(); }}
                className="flex items-center gap-2 px-2 py-1"
              >
                <input
                  aria-label="AI prompt"
                  placeholder="Ask AI to edit..."
                  value={aiPrompt}
                  onChange={e => setAiPrompt(e.target.value)}
                  className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500 w-56"
                />
                <button
                  type="submit"
                  disabled={isApplyingAiEdit || !aiPrompt.trim() || !hasEditableFilePointers}
                  className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed text-white rounded text-xs font-medium transition-colors"
                >
                  {isApplyingAiEdit ? 'Applying...' : 'AI Edit'}
                </button>
              </form>
            )}
          </div>
          <div className="flex-1 w-full relative">
            <Editor
              height="100%"
              defaultLanguage="python"
              theme="vs-dark"
              value={code}
              onChange={(val) => handleCodeChange(val || '')}
              options={{
                minimap: { enabled: false },
                fontSize: 14,
                wordWrap: 'on',
                folding: true,
                lineNumbersMinChars: 3,
                scrollBeyondLastLine: false,
                padding: { top: 16 }
              }}
            />
          </div>
        </div>
        <div className="w-1/3 flex flex-col bg-slate-950">
          <div className="px-3 py-1.5 border-b border-slate-800 text-xs font-mono text-slate-500 flex justify-between items-center bg-slate-900/50">
            <div className="flex gap-4">
              <button 
                onClick={() => setActivePane('output')}
                className={`transition-colors ${activePane === 'output' ? 'text-indigo-400 font-bold' : 'hover:text-slate-300'}`}
              >
                Compiler Output
              </button>
              <button 
                onClick={() => setActivePane('history')}
                className={`transition-colors flex items-center gap-1 ${activePane === 'history' ? 'text-indigo-400 font-bold' : 'hover:text-slate-300'}`}
              >
                Git History
                {gitStatus.history && <span className="px-1.5 py-0.5 rounded-full bg-slate-800 text-[10px]">{gitStatus.history.length}</span>}
              </button>
            </div>
            {activePane === 'output' && (
              <div className="flex items-center gap-2">
                {failedCompileRetry && (
                  <button
                    type="button"
                    onClick={() => void startCompile(failedCompileRetry.code, 'manual')}
                    disabled={isCompiling || isGuest}
                    className="hover:text-slate-300 px-2 py-0.5 rounded bg-slate-800 border border-slate-700 disabled:opacity-50"
                  >
                    Try again
                  </button>
                )}
                <button onClick={() => setLog('')} className="hover:text-slate-300 px-2 py-0.5 rounded bg-slate-800 border border-slate-700">Clear</button>
              </div>
            )}
          </div>
          
          <div className="flex-1 overflow-auto">
            {activePane === 'output' ? (
              <pre className="p-4 font-mono text-xs text-slate-300 whitespace-pre-wrap">
                {log || 'Waiting for compilation...'}
              </pre>
            ) : (
              <div className="p-3 flex flex-col gap-2">
                {!gitStatus.is_git ? (
                  <div className="text-sm text-slate-500 text-center mt-4">{gitStatus.label || 'Not a git repository.'}</div>
                ) : !gitStatus.history || gitStatus.history.length === 0 ? (
                  <div className="text-sm text-slate-500 text-center mt-4">No commits yet.</div>
                ) : (
                  gitStatus.history.map((line, i) => {
                    const match = line.match(/^([a-f0-9]+)\s+(.*)$/);
                    if (!match) return <div key={i} className="text-xs font-mono text-slate-400 p-2 bg-slate-900/50 rounded">{line}</div>;
                    return (
                      <div key={i} className="flex gap-3 text-xs font-mono p-2 bg-slate-900/80 hover:bg-slate-800 rounded border border-slate-800 transition-colors">
                        <span className="text-emerald-500 font-bold shrink-0">{match[1]}</span>
                        <span className="text-slate-300 truncate">{match[2]}</span>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
