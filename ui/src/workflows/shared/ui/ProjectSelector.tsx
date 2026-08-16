import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../auth/AuthProvider';
import { resolveWorkflowServerUrl } from '../apiConfig';
import { createProjectStorage } from '../projectStorage';
import { ACTIVE_PROJECT_POLL_INTERVAL_MS, getPollingDelay, shouldRunPollingRequest } from '../polling';

export const ACTIVE_PROJECT_CHANGED_EVENT = 'tertius:active-project-changed';

const errorMessage = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback

export const suggestedProjectName = (filename: string) => {
  const basename = filename.replace(/\.3mf$/i, '')
  const normalized = basename
    .replace(/[^A-Za-z0-9_.-]+/g, '_')
    .slice(0, 80)
    .replace(/^[._-]+|[._-]+$/g, '')
  return normalized || 'imported_3mf'
}

export const ProjectSelector: React.FC = () => {
  const { authMode, getAccessToken } = useAuth();
  const serverUrl = resolveWorkflowServerUrl('intus', import.meta.env?.VITE_API_URL);
  const storage = React.useMemo(
    () => createProjectStorage({ authMode, serverUrl, getAccessToken }),
    [authMode, getAccessToken, serverUrl],
  );
  
  const [projects, setProjects] = useState<string[]>([]);
  const [activeProject, setActiveProject] = useState<string>('');
  const [isCreating, setIsCreating] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [isImporting, setIsImporting] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importProjectName, setImportProjectName] = useState('');
  const [importPending, setImportPending] = useState(false);
  const [gitStatus, setGitStatus] = useState<{ is_git: boolean, commit?: string, history?: string[], label?: string }>({ is_git: false });

  const fetchGitStatus = useCallback(async (name: string) => {
    try {
      const data = await storage.getHistory(name);
      setGitStatus(data);
    } catch {
      setGitStatus({ is_git: false });
    }
  }, [storage]);

  const selectProject = useCallback(async (name: string) => {
    try {
      await storage.activateProject(name);
      setActiveProject(name);
      fetchGitStatus(name);
      window.dispatchEvent(new CustomEvent(ACTIVE_PROJECT_CHANGED_EVENT, { detail: { activeProject: name } }));
      return true;
    } catch (e) {
      alert(errorMessage(e, "Network error selecting project"));
      return false;
    }
  }, [storage, fetchGitStatus]);

  const refreshProjectList = useCallback(async () => {
    const list = await storage.listProjects();
    setProjects(list);
    return list;
  }, [storage]);

  const fetchProjects = useCallback(async (selectName?: string) => {
    try {
      const list = await refreshProjectList();
      
      let currentBackendProject = activeProject;
      if (!currentBackendProject) {
         try {
            currentBackendProject = await storage.getActiveProject();
         } catch (e) {}
      }
      
      let target = selectName;
      if (!target && !currentBackendProject && list.length > 0) {
          target = list[0];
      }
      
      if (target && target !== currentBackendProject) {
        selectProject(target);
      } else if (currentBackendProject && currentBackendProject !== activeProject) {
        setActiveProject(currentBackendProject);
        fetchGitStatus(currentBackendProject);
      }
    } catch (e) {
      console.error("Failed to fetch projects");
    }
  }, [storage, activeProject, selectProject, fetchGitStatus, refreshProjectList]);

  // Sync active project with backend (in case another tab changed it, though this is the primary selector)
  useEffect(() => {
    let isMounted = true;
    const fetchActive = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const projectName = await storage.getActiveProject();
        if (projectName && projectName !== activeProject && isMounted) {
          setActiveProject(projectName);
          fetchGitStatus(projectName);
        }
      } catch (e) {
      }
    };

    fetchActive();
    const interval = setInterval(fetchActive, getPollingDelay(ACTIVE_PROJECT_POLL_INTERVAL_MS));
    return () => {
        isMounted = false;
        clearInterval(interval);
    };
  }, [storage, activeProject, fetchGitStatus]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const handleNewProjectSubmit = async () => {
    const name = newProjectName.trim();
    if (!name) return;
    
    try {
      await storage.createProject(name);
      fetchProjects(name);
      setIsCreating(false);
      setNewProjectName('');
    } catch (e) {
      alert(errorMessage(e, "Network error creating project"));
    }
  };

  const handleImportSubmit = async () => {
    const name = importProjectName.trim();
    if (!importFile || !name || importPending) return;
    setImportPending(true);
    try {
      const result = await storage.import3mf(importFile, name);
      setProjects((current) => current.includes(result.project)
        ? current
        : [...current, result.project]);
      try {
        await refreshProjectList();
      } catch (error) {
        console.error("Failed to refresh projects after import", error);
      }
      setIsImporting(false);
      setImportFile(null);
      setImportProjectName('');
      await selectProject(result.project);
    } catch (e) {
      alert(errorMessage(e, '3MF import failed'));
    } finally {
      setImportPending(false);
    }
  };

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select 
        className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs font-medium focus:outline-none focus:border-indigo-500 max-w-[150px]"
        value={activeProject}
        onChange={(e) => selectProject(e.target.value)}
      >
        <option value="" disabled>Select project...</option>
        {projects.map(p => <option key={p} value={p}>{p}</option>)}
      </select>

      {/* Git LED Badge */}
      {gitStatus.is_git ? (
        <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-slate-800/50 border border-slate-700">
          <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]"></span>
          <span className="text-[10px] font-mono text-slate-300">Git: {gitStatus.commit}</span>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-slate-800/50 border border-slate-700 opacity-50">
          <span className="w-2 h-2 rounded-full bg-slate-600"></span>
          <span className="text-[10px] font-mono text-slate-400">{gitStatus.label || 'No Git'}</span>
        </div>
      )}
      
      {isCreating ? (
        <form onSubmit={(e) => { e.preventDefault(); handleNewProjectSubmit(); }} className="flex items-center gap-1">
          <input 
            autoFocus
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[10px] focus:outline-none focus:border-indigo-500 w-24"
            value={newProjectName}
            onChange={e => setNewProjectName(e.target.value)}
            placeholder="Name..."
          />
          <button type="submit" className="px-2 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-[10px] transition-colors text-white font-medium">Add</button>
          <button type="button" onClick={() => setIsCreating(false)} className="px-1 text-slate-400 hover:text-slate-200 text-[10px]">✕</button>
        </form>
      ) : (
        <button 
          onClick={() => setIsCreating(true)}
          className="px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-[10px] transition-colors"
          title="New Project"
        >
          ➕ New
        </button>
      )}
      {authMode === 'authenticated' && (
        <button
          onClick={() => setIsImporting(true)}
          className="px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-[10px] transition-colors"
        >
          Import 3MF
        </button>
      )}
      {isImporting && (
        <div role="dialog" aria-modal="true" aria-label="Import 3MF project" className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70">
          <form onSubmit={(event) => { event.preventDefault(); void handleImportSubmit(); }} className="w-80 space-y-3 rounded border border-slate-700 bg-slate-900 p-4 shadow-2xl">
            <h2 className="text-sm font-semibold text-slate-100">Import 3MF</h2>
            <label className="block text-xs text-slate-300">
              3MF file
              <input aria-label="3MF file" type="file" accept=".3mf,application/vnd.ms-package.3dmanufacturing-3dmodel+xml" onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                setImportFile(file);
                if (file && !importProjectName) setImportProjectName(suggestedProjectName(file.name));
              }} className="mt-1 block w-full text-xs" />
            </label>
            <label className="block text-xs text-slate-300">
              Project name
              <input aria-label="Imported project name" value={importProjectName} onChange={(event) => setImportProjectName(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1" />
            </label>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setIsImporting(false)} className="px-2 py-1 text-xs text-slate-300">Cancel</button>
              <button type="submit" disabled={!importFile || !importProjectName.trim() || importPending} className="rounded bg-indigo-600 px-2 py-1 text-xs text-white disabled:opacity-50">{importPending ? 'Importing…' : 'Import project'}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
