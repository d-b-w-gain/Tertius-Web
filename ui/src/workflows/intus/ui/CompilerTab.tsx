import React, { useState, useEffect, useRef } from 'react';
import Editor from '@monaco-editor/react';


export const CompilerTab: React.FC<{ serverUrl: string, isActive?: boolean }> = ({ serverUrl, isActive = true }) => {
  const [projects, setProjects] = useState<string[]>([]);
  const [activeProject, setActiveProject] = useState<string>('');
  const [code, setCode] = useState<string>('');
  const [format, setFormat] = useState<string>('gltf');
  const [quality, setQuality] = useState<string>('high');
  const [log, setLog] = useState<string>('');
  const [isCompiling, setIsCompiling] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  
  const [files, setFiles] = useState<string[]>(['design.py']);
  const [activeFile, setActiveFile] = useState<string>('design.py');
  const [isCreatingFile, setIsCreatingFile] = useState(false);
  const [newFileName, setNewFileName] = useState('');
  
  // Git UI State
  const [gitStatus, setGitStatus] = useState<{ is_git: boolean, commit?: string, history?: string[] }>({ is_git: false });
  const [activePane, setActivePane] = useState<'output' | 'history'>('output');
  
  const mtimeRef = useRef<number>(0);
  const isCompilingRef = useRef<boolean>(false);

  useEffect(() => {
    isCompilingRef.current = isCompiling;
  }, [isCompiling]);

  useEffect(() => {
    fetchProjects();
  }, []);

  // Poll for external file changes (e.g. from Artus)
  useEffect(() => {
    if (!activeProject || !isActive) return;
    
    const checkSync = async () => {
      if (isCompilingRef.current) return;
      try {
        const res = await fetch(`${serverUrl}/projects/${activeProject}/status?file=${activeFile}`);
        if (!res.ok) return;
        const data = await res.json();
        
        if (data.mtime) {
          if (mtimeRef.current === 0) {
             mtimeRef.current = data.mtime; // Initial baseline
          } else if (data.mtime > mtimeRef.current) {
             // File changed on disk! Sync it.
             mtimeRef.current = data.mtime;
             
             const codeRes = await fetch(`${serverUrl}/projects/${activeProject}/code?file=${activeFile}`);
             if (!codeRes.ok) return;
             const codeData = await codeRes.json();
             const newCode = codeData.code || '';
             setCode(newCode);
             setLog('External change detected (Artus). Auto-compiling...');
             
             // Trigger auto-compile silently
             setIsCompiling(true);
             try {
               const compRes = await fetch(`${serverUrl}/projects/${activeProject}/compile`, {
                 method: 'POST',
                 headers: { 'Content-Type': 'application/json' },
                 body: JSON.stringify({ code: newCode, export_format: format, quality, file: activeFile })
               });
               const compData = await compRes.json();
               if (compData.success) {
                 setLog(prev => prev + `\n[SUCCESS] Auto-compiled to ${compData.file}`);
                 // Update mtime to prevent loop
                 const postStatusRes = await fetch(`${serverUrl}/projects/${activeProject}/status`);
                 if (postStatusRes.ok) {
                   const postStatus = await postStatusRes.json();
                   if (postStatus.mtime) mtimeRef.current = postStatus.mtime;
                 }
                 fetchGitStatus(activeProject);
               } else {
                 setLog(prev => prev + `\n[ERROR] ${compData.short}`);
               }
             } catch (e) {
                 setLog(prev => prev + `\n[ERROR] Auto-compile failed.`);
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
  }, [activeProject, serverUrl, format, isActive, activeFile]);

  const fetchGitStatus = async (name: string) => {
    try {
      const res = await fetch(`${serverUrl}/projects/${name}/git_status`);
      if (res.ok) {
        const data = await res.json();
        setGitStatus(data);
      } else {
        setGitStatus({ is_git: false });
      }
    } catch {
      setGitStatus({ is_git: false });
    }
  };

  const fetchProjects = async (selectName?: string) => {
    try {
      const res = await fetch(`${serverUrl}/projects`);
      const data = await res.json();
      const list = data.projects || [];
      setProjects(list);
      
      let target = selectName;
      if (!target) {
        const last = localStorage.getItem('intus_last_project');
        if (last && list.includes(last)) target = last;
        else if (list.length > 0) target = list[0];
      }
      
      if (target) {
        selectProject(target);
      }
    } catch (e) {
      console.error("Failed to fetch projects");
    }
  };

  const selectProject = async (name: string) => {
    setActiveProject(name);
    localStorage.setItem('intus_last_project', name);
    mtimeRef.current = 0; // Reset baseline for new project
    try {
      const filesRes = await fetch(`${serverUrl}/projects/${name}/files`);
      const filesData = await filesRes.json();
      const fileList = filesData.files || ['design.py'];
      setFiles(fileList);
      
      const fileToLoad = fileList.includes('design.py') ? 'design.py' : fileList[0];
      setActiveFile(fileToLoad);
      
      const res = await fetch(`${serverUrl}/projects/${name}/code?file=${fileToLoad}`);
      const data = await res.json();
      setCode(data.code || '');
      setLog(`Loaded project: ${name}`);
      fetchGitStatus(name);
    } catch (e) {
      setLog(`Failed to load project: ${name}`);
    }
  };

  const switchFile = async (fileName: string) => {
    if (fileName === activeFile) return;
    
    // Auto-save current before switching
    try {
      await fetch(`${serverUrl}/projects/${activeProject}/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, file: activeFile })
      });
    } catch(e) {}

    setActiveFile(fileName);
    mtimeRef.current = 0; // Prevent false-positive sync when switching files
    try {
      const res = await fetch(`${serverUrl}/projects/${activeProject}/code?file=${fileName}`);
      const data = await res.json();
      setCode(data.code || '');
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
      await fetch(`${serverUrl}/projects/${activeProject}/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: "", file: name })
      });
      
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
      await fetch(`${serverUrl}/projects/${activeProject}/file?file=${fileName}`, {
        method: 'DELETE'
      });
      
      const newFiles = files.filter(f => f !== fileName);
      setFiles(newFiles);
      
      if (activeFile === fileName) {
        switchFile('design.py');
      }
    } catch (e) {
      alert("Network error deleting file");
    }
  };

  const handleNewProjectSubmit = async () => {
    const name = newProjectName.trim();
    if (!name) return;
    
    try {
      const res = await fetch(`${serverUrl}/projects/${name}/new`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        fetchProjects(name);
        setIsCreating(false);
        setNewProjectName('');
      } else {
        alert(data.error || "Failed to create project");
      }
    } catch (e) {
      alert("Network error creating project");
    }
  };

  const handleCompile = async () => {
    if (!activeProject) return;
    setIsCompiling(true);
    setLog(`Compiling ${activeProject}...`);
    try {
      const res = await fetch(`${serverUrl}/projects/${activeProject}/compile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, export_format: format, quality, file: activeFile })
      });
      const data = await res.json();
      if (data.success) {
        setLog(`[SUCCESS] Compiled and exported to ${data.file}`);
        // Prevent sync loop by updating baseline
        const postStatusRes = await fetch(`${serverUrl}/projects/${activeProject}/status`);
        if (postStatusRes.ok) {
          const postStatus = await postStatusRes.json();
          if (postStatus.mtime) mtimeRef.current = postStatus.mtime;
        }
        fetchGitStatus(activeProject);
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
        <select 
          className="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm font-medium focus:outline-none focus:border-indigo-500"
          value={activeProject}
          onChange={(e) => selectProject(e.target.value)}
        >
          {projects.map(p => <option key={p} value={p}>{p}</option>)}
        </select>

        {/* Git LED Badge */}
        {gitStatus.is_git ? (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-slate-800/50 border border-slate-700">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]"></span>
            <span className="text-xs font-mono text-slate-300">Git: {gitStatus.commit}</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-slate-800/50 border border-slate-700 opacity-50">
            <span className="w-2 h-2 rounded-full bg-slate-600"></span>
            <span className="text-xs font-mono text-slate-400">No Git</span>
          </div>
        )}
        
        {isCreating ? (
          <form onSubmit={(e) => { e.preventDefault(); handleNewProjectSubmit(); }} className="flex items-center gap-2">
            <input 
              autoFocus
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm focus:outline-none focus:border-indigo-500"
              value={newProjectName}
              onChange={e => setNewProjectName(e.target.value)}
              placeholder="Project name..."
            />
            <button type="submit" className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-sm transition-colors text-white font-medium">Create</button>
            <button type="button" onClick={() => setIsCreating(false)} className="px-2 py-1 text-slate-400 hover:text-slate-200 text-sm">Cancel</button>
          </form>
        ) : (
          <button 
            onClick={() => setIsCreating(true)}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-sm transition-colors"
          >
            ➕ New Project
          </button>
        )}

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
              <option value="gltf">GLTF</option>
            </select>
          </div>
        </div>

        <button 
          onClick={handleCompile}
          disabled={isCompiling}
          className={`px-4 py-1.5 rounded text-sm font-bold shadow-lg transition-all ${
            isCompiling 
              ? 'bg-indigo-600/50 text-indigo-200 cursor-not-allowed' 
              : 'bg-indigo-600 hover:bg-indigo-500 text-white'
          }`}
        >
          {isCompiling ? 'Compiling...' : '⚙️ Compile & Export'}
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
              onChange={(val) => setCode(val || '')}
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
                  <div className="text-sm text-slate-500 text-center mt-4">Not a git repository.</div>
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
