import React, { useState, useEffect, useMemo, useRef } from 'react';
import Editor from '@monaco-editor/react';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import { ProjectSelector } from '../../shared/ui/ProjectSelector';
import { createProjectStorage } from '../../shared/projectStorage';
import { GUEST_WORKSPACE_CHANGED_EVENT } from '../../shared/guestWorkspace';


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
  const [quality, setQuality] = useState<string>('high');
  const [log, setLog] = useState<string>('');
  const [isCompiling, setIsCompiling] = useState(false);
  const [autoCompile, setAutoCompile] = useState<boolean>(true);
  
  const [files, setFiles] = useState<string[]>(['design.py']);
  const [activeFile, setActiveFile] = useState<string>('design.py');
  const [isCreatingFile, setIsCreatingFile] = useState(false);
  const [newFileName, setNewFileName] = useState('');
  
  // Git UI State
  const [gitStatus, setGitStatus] = useState<{ is_git: boolean, commit?: string, history?: string[], label?: string }>({ is_git: false });
  const [activePane, setActivePane] = useState<'output' | 'history'>('output');
  
  const mtimeRef = useRef<number>(0);
  const isCompilingRef = useRef<boolean>(false);

  useEffect(() => {
    isCompilingRef.current = isCompiling;
  }, [isCompiling]);

  useEffect(() => {
    let isMounted = true;
    const loadActiveProject = async () => {
      try {
        const projectName = await storage.getActiveProject();
        if (!projectName || !isMounted || projectName === activeProject) {
          return;
        }

        setActiveProject(projectName);
        fetchGitStatus(projectName);
        mtimeRef.current = 0;

        const projectFiles = await storage.listFiles(projectName);
        const nextFiles = projectFiles.length > 0 ? projectFiles : ['design.py'];
        const nextFile = nextFiles.includes(activeFile) ? activeFile : nextFiles[0];
        const nextCode = await storage.loadCode(projectName, nextFile);
        if (isMounted) {
          setFiles(nextFiles);
          setActiveFile(nextFile);
          setCode(nextCode);
        }
      } catch (e) {}
    };
    
    loadActiveProject();
    const interval = isGuest ? undefined : setInterval(loadActiveProject, 2000);
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
  }, [storage, activeProject, activeFile, isGuest]);

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
        const projectFiles = await storage.listFiles(detail.activeProject!);
        const nextFile = detail.activeFile && projectFiles.includes(detail.activeFile) ? detail.activeFile : projectFiles[0] || 'design.py';
        const nextCode = await storage.loadCode(detail.activeProject!, nextFile);
        setActiveProject(detail.activeProject!);
        setFiles(projectFiles.length > 0 ? projectFiles : ['design.py']);
        setActiveFile(nextFile);
        setCode(nextCode);
        mtimeRef.current = 0;
      })();
    };
    window.addEventListener('tertius:guest-imported', handleImported);
    return () => window.removeEventListener('tertius:guest-imported', handleImported);
  }, [isGuest, storage]);

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
             
             setLog('External change detected (Artus). Auto-compiling...');
             
             // Trigger auto-compile silently
             setIsCompiling(true);
             try {
               const compRes = await apiFetch(`${serverUrl}/projects/${activeProject}/compile`, getAccessToken, {
                 method: 'POST',
                 headers: { 'Content-Type': 'application/json' },
                 body: JSON.stringify({ code: newCode, export_format: format, quality, file: activeFile })
               });
               const compData = await compRes.json();
               if (compData.success) {
                 setLog(prev => prev + `\n[SUCCESS] Auto-compiled to ${compData.file}`);
                 // Update mtime to prevent loop
                 const postStatusRes = await apiFetch(`${serverUrl}/projects/${activeProject}/status`, getAccessToken);
                 if (postStatusRes.ok) {
                   const postStatus = await postStatusRes.json();
                   if (postStatus.mtime) mtimeRef.current = postStatus.mtime;
                 }
                 fetchGitStatus(activeProject);
               } else {
                 setLog(prev => prev + `\n[ERROR] ${compData.short}`);
                 setAutoCompile(false);
               }
             } catch (e) {
                 setLog(prev => prev + `\n[ERROR] Auto-compile failed.`);
                 setAutoCompile(false);
             }
             setIsCompiling(false);
          }
        }
      } catch (e) {
        // Silently fail if server is busy
      }
    };
    
    const interval = setInterval(checkSync, 1000);
    return () => clearInterval(interval);
  }, [activeProject, serverUrl, format, quality, isActive, activeFile, getAccessToken, autoCompile, isGuest, storage]);

  const fetchGitStatus = async (name: string) => {
    try {
      const data = await storage.getHistory(name);
      setGitStatus(data);
    } catch {
      setGitStatus({ is_git: false });
    }
  };

  const switchFile = async (fileName: string) => {
    if (fileName === activeFile) return;
    
    // Auto-save current before switching
    try {
      await storage.saveCode(activeProject, activeFile, code);
    } catch(e) {}

    setActiveFile(fileName);
    mtimeRef.current = 0; // Prevent false-positive sync when switching files
    try {
      const nextCode = await storage.loadCode(activeProject, fileName);
      setCode(nextCode || '');
    } catch (e) {
      setLog(`Failed to load file: ${fileName}`);
    }
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
      
      setFiles([...files, name]);
      setIsCreatingFile(false);
      setNewFileName('');
      switchFile(name);
    } catch (e) {
      alert("Network error creating file");
    }
  };

  const handleDeleteFile = async (fileName: string) => {
    if (fileName === 'design.py') return;
    if (!window.confirm(`Are you sure you want to delete ${fileName}?`)) return;
    
    try {
      await storage.deleteFile(activeProject, fileName);
      
      const newFiles = files.filter(f => f !== fileName);
      setFiles(newFiles);
      
      if (activeFile === fileName) {
        switchFile('design.py');
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
    if (!activeProject) return;
    setIsCompiling(true);
    setLog(`Compiling ${activeProject}...`);
    try {
      const res = await apiFetch(`${serverUrl}/projects/${activeProject}/compile`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, export_format: format, quality, file: activeFile })
      });
      const data = await res.json();
      if (data.success) {
        setLog(`[SUCCESS] Compiled and exported to ${data.file}`);
        // Prevent sync loop by updating baseline
        const postStatusRes = await apiFetch(`${serverUrl}/projects/${activeProject}/status`, getAccessToken);
        if (postStatusRes.ok) {
          const postStatus = await postStatusRes.json();
          if (postStatus.mtime) mtimeRef.current = postStatus.mtime;
        }
        fetchGitStatus(activeProject);
        setAutoCompile(true); // Re-enable on manual success
      } else {
        setLog(`[ERROR] ${data.short}\n\n${data.error}`);
      }
    } catch (e) {
      setLog(`[FATAL] Failed to reach server during compile.`);
    }
    setIsCompiling(false);
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
              <option value="medium">Medium</option>
              <option value="low">Low</option>
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
              <button 
                onClick={() => setIsCreatingFile(true)}
                className="px-3 py-2 text-xs font-mono text-slate-500 hover:text-slate-300 hover:bg-slate-900 transition-colors"
                title="New File"
              >
                +
              </button>
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
              <button onClick={() => setLog('')} className="hover:text-slate-300 px-2 py-0.5 rounded bg-slate-800 border border-slate-700">Clear</button>
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
