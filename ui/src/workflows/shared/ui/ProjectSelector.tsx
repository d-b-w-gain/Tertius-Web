import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';

export const ProjectSelector: React.FC = () => {
  const { getAccessToken } = useAuth();
  const serverUrl = '/proxy/api/intus'; // Intus handles project management
  
  const [projects, setProjects] = useState<string[]>([]);
  const [activeProject, setActiveProject] = useState<string>('');
  const [isCreating, setIsCreating] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [gitStatus, setGitStatus] = useState<{ is_git: boolean, commit?: string, history?: string[] }>({ is_git: false });

  // Sync active project with backend (in case another tab changed it, though this is the primary selector)
  useEffect(() => {
    let isMounted = true;
    const fetchActive = async () => {
      try {
        const res = await apiFetch(`${serverUrl}/project_name`, getAccessToken);
        if (res.ok && isMounted) {
           const data = await res.json();
           if (data.project_name && data.project_name !== activeProject) {
               setActiveProject(data.project_name);
               fetchGitStatus(data.project_name);
           }
        }
      } catch (e) {
      }
    };
    
    fetchActive();
    const interval = setInterval(fetchActive, 2000);
    return () => {
        isMounted = false;
        clearInterval(interval);
    };
  }, [serverUrl, getAccessToken, activeProject]);

  useEffect(() => {
    fetchProjects();
  }, [getAccessToken]);

  const fetchGitStatus = async (name: string) => {
    try {
      const res = await apiFetch(`${serverUrl}/projects/${name}/git_status`, getAccessToken);
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
      const res = await apiFetch(`${serverUrl}/projects`, getAccessToken);
      if (!res.ok) return;
      const data = await res.json();
      const list = data.projects || [];
      setProjects(list);
      
      let currentBackendProject = activeProject;
      if (!currentBackendProject) {
         try {
            const activeRes = await apiFetch(`${serverUrl}/project_name`, getAccessToken);
            if (activeRes.ok) {
                const activeData = await activeRes.json();
                currentBackendProject = activeData.project_name;
            }
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
  };

  const selectProject = async (name: string) => {
    setActiveProject(name);
    try {
      await apiFetch(`${serverUrl}/projects/${name}/activate`, getAccessToken, { method: 'POST' });
      fetchGitStatus(name);
    } catch (e) {
      alert("Network error selecting project");
    }
  };

  const handleNewProjectSubmit = async () => {
    const name = newProjectName.trim();
    if (!name) return;
    
    try {
      const res = await apiFetch(`${serverUrl}/projects/${name}/new`, getAccessToken, { method: 'POST' });
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
          <span className="text-[10px] font-mono text-slate-400">No Git</span>
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
