import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../auth/AuthProvider';
import { resolveWorkflowServerUrl } from '../apiConfig';
import { createProjectStorage } from '../projectStorage';
import { ACTIVE_PROJECT_POLL_INTERVAL_MS, getPollingDelay, shouldRunPollingRequest } from '../polling';

export const ACTIVE_PROJECT_CHANGED_EVENT = 'tertius:active-project-changed';

const errorMessage = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback

export const ProjectSelector: React.FC<{ isActive?: boolean }> = ({ isActive = true }) => {
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
    setActiveProject(name);
    try {
      await storage.activateProject(name);
      fetchGitStatus(name);
      window.dispatchEvent(new CustomEvent(ACTIVE_PROJECT_CHANGED_EVENT, { detail: { activeProject: name } }));
    } catch (e) {
      alert(errorMessage(e, "Network error selecting project"));
    }
  }, [storage, fetchGitStatus]);

  const fetchProjects = useCallback(async (selectName?: string) => {
    try {
      const list = await storage.listProjects();
      setProjects(list);
      
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
  }, [storage, activeProject, selectProject, fetchGitStatus]);

  // Sync active project with backend (in case another tab changed it, though this is the primary selector)
  useEffect(() => {
    if (!isActive) return;

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
  }, [storage, activeProject, fetchGitStatus, isActive]);

  useEffect(() => {
    if (!isActive) return;

    fetchProjects();
  }, [fetchProjects, isActive]);

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
    </div>
  );
};
