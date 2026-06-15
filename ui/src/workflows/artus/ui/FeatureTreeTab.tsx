import React, { useCallback, useEffect, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import { ProjectSelector } from '../../shared/ui/ProjectSelector';
import { GuestWorkflowNotice } from '../../shared/ui/GuestWorkflowNotice';
import {
  MODEL_STATUS_POLL_INTERVAL_MS,
  PROJECT_DATA_POLL_INTERVAL_MS,
  getPollingDelay,
  shouldRunPollingRequest,
} from '../../shared/polling';

// Helper component for the recursive assembly tree
const TreeNode: React.FC<{
  node: THREE.Object3D;
  depth: number;
  selectedId: string | null;
  onSelect: (node: THREE.Object3D, isDouble: boolean) => void;
}> = ({ node, depth, selectedId, onSelect }) => {
  const [expanded, setExpanded] = useState(true);
  
  const isMesh = (node as THREE.Mesh).isMesh;
  const isGroup = node.type === 'Group' || node.type === 'Object3D';
  const hasChildren = node.children && node.children.length > 0;
  
  if (!isMesh && !isGroup) return null;
  
  const displayName = node.name || (isMesh ? 'Mesh' : 'Component');

  return (
    <div className="flex flex-col font-mono text-xs">
       <div 
         className={`flex items-center py-0.5 px-2 cursor-pointer transition-colors ${selectedId === node.uuid || selectedId === node.name ? 'bg-indigo-900/40 border border-indigo-500/50 rounded shadow-[inset_0_0_10px_rgba(99,102,241,0.2)]' : 'hover:bg-slate-800/50'}`}
         style={{ paddingLeft: `${depth * 16 + 8}px` }}
         onClick={() => onSelect(node, false)}
         onDoubleClick={() => onSelect(node, true)}
       >
          {hasChildren ? (
            <span onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }} className="w-4 mr-1 text-[10px] text-slate-500 hover:text-slate-300 focus:outline-none flex-shrink-0 flex items-center justify-center opacity-70">
              {expanded ? '▼' : '▶'}
            </span>
          ) : <span className="w-4 mr-1 inline-block" />}
          <span className="text-xs font-medium truncate select-none text-slate-300">{displayName}</span>
       </div>
       {expanded && hasChildren && node.children.map(c => (
         <div key={c.uuid} className="flex flex-col relative">
           <div 
             className="absolute left-0 top-0 bottom-0 w-px bg-slate-800/50" 
             style={{ marginLeft: `${depth * 16 + 14}px` }}
           />
           <TreeNode node={c} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} />
         </div>
       ))}
    </div>
  );
};

interface Feature {
  name: string;
  value: string | number | boolean;
  type: string;
  description: string;
}

interface OperationNode {
  type: string;
  name: string;
  as_name?: string;
  arguments?: string[];
  dependencies?: string[];
  children?: OperationNode[];
}

const getIcon = (name: string) => {
  if (name.includes('BuildPart')) return '🧊';
  if (name.includes('BuildSketch')) return '✏️';
  if (name.includes('BuildLine')) return '📏';
  if (name.includes('Locations')) return '📍';
  if (name.includes('Rectangle')) return '🟦';
  if (name.includes('Circle')) return '⭕';
  if (name.includes('SlotOverall')) return '💊';
  if (name.includes('extrude')) return '⬆️';
  if (name.includes('fillet')) return '🔪';
  return '⚡';
};

const RenderOperation: React.FC<{ node: OperationNode; depth: number; highlightedNode?: string | null }> = ({ node, depth, highlightedNode }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const isContext = node.type === 'Context';
  const hasChildren = node.children && node.children.length > 0;
  
  const displayName = node.as_name || node.name;
  const tooltip = node.as_name ? `Alias for ${node.name}` : node.name;
  const isHighlighted = highlightedNode && displayName === highlightedNode;
  
  return (
    <div className="flex flex-col font-mono text-xs">
      <div 
        className={`flex items-center py-0.5 px-2 cursor-default transition-colors group ${isHighlighted ? 'bg-indigo-900/40 border border-indigo-500/50 rounded shadow-[inset_0_0_10px_rgba(99,102,241,0.2)]' : 'hover:bg-slate-800/50'}`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {isContext && hasChildren ? (
          <button 
            onClick={() => setIsExpanded(!isExpanded)}
            className="w-4 mr-1 text-[10px] text-slate-500 hover:text-slate-300 focus:outline-none flex-shrink-0 flex items-center justify-center opacity-70 group-hover:opacity-100"
          >
            {isExpanded ? '▼' : '▶'}
          </button>
        ) : (
          <span className="w-4 mr-1 inline-block"></span>
        )}
        
        <div className="flex items-center gap-1.5 min-w-[160px]" title={tooltip}>
          <span className="select-none text-[10px] flex-shrink-0 opacity-80">
            {getIcon(node.name)}
          </span>
          <span className={`px-1 py-0 rounded leading-tight ${
            isContext 
              ? 'font-bold bg-blue-950/40 text-blue-400 border border-blue-500/20' 
              : 'bg-yellow-950/30 text-yellow-400/90 border border-yellow-500/20'
          }`}>
            {displayName}
          </span>
        </div>
        
        <div className="flex-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 ml-2">
          {node.arguments && node.arguments.length > 0 && (
            <div className="text-slate-500 truncate max-w-[200px]" title={node.arguments.join(', ')}>
              ({node.arguments.join(', ')})
            </div>
          )}
          
          {node.dependencies && node.dependencies.length > 0 && (
            <div className="flex gap-1">
              {node.dependencies.map((dep, i) => (
                <span key={i} className="text-[9px] px-1 bg-emerald-950/50 text-emerald-400/80 rounded border border-emerald-900/40 leading-tight">
                  {dep}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
      
      {isExpanded && node.children && node.children.length > 0 && (
        <div className="flex flex-col relative">
          {/* Subtle connecting guide line for children */}
          <div 
            className="absolute left-0 top-0 bottom-0 w-px bg-slate-800/50" 
            style={{ marginLeft: `${depth * 16 + 14}px` }}
          />
          {node.children.map((child, i) => (
            <RenderOperation 
              key={i} 
              node={child} 
              depth={depth + 1}
              highlightedNode={highlightedNode}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export const FeatureTreeTab: React.FC<{ serverUrl: string; isActive?: boolean }> = (props) => {
  const { authMode, login } = useAuth();
  if (authMode === 'guest') {
    return (
      <GuestWorkflowNotice
        title="Log in to inspect and modify features"
        message="Artus feature inspection and AI edits use authenticated project APIs."
        onLogin={login}
      />
    );
  }
  return <AuthenticatedFeatureTreeTab {...props} />;
};

const AuthenticatedFeatureTreeTab: React.FC<{ serverUrl: string; isActive?: boolean }> = ({ serverUrl, isActive = true }) => {
  const { getAccessToken } = useAuth();
  const [features, setFeatures] = useState<Feature[]>([]);
  const [operations, setOperations] = useState<OperationNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  
  // AI State
  const [prompt, setPrompt] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [aiMessage, setAiMessage] = useState<string | null>(null);
  
  // Local variable edits
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [activePanel, setActivePanel] = useState<'variables' | 'operations' | 'assembly'>('variables');
  
  const [extusUrl, setExtusUrl] = useState<string>('');
  const [sceneGraph, setSceneGraph] = useState<THREE.Object3D | null>(null);
  
  const [highlightedNode, setHighlightedNode] = useState<string | null>(null);

  const handleSelectNode = (node: THREE.Object3D, isDouble: boolean) => {
     const nodeName = node.name || '';
     
     if (isDouble) {
        if (highlightedNode === nodeName) {
           setHighlightedNode(null);
           localStorage.removeItem('tertius_selected_node');
        } else {
           setHighlightedNode(nodeName);
           localStorage.setItem('tertius_selected_node', nodeName);
        }
     } else {
        setHighlightedNode(nodeName);
        localStorage.setItem('tertius_selected_node', nodeName);
     }
     window.dispatchEvent(new Event('storage'));
  };

  // Listen to Extus selections via LocalStorage
  useEffect(() => {
    const handleStorage = () => {
      const selected = localStorage.getItem('tertius_selected_node');
      setHighlightedNode(selected || null);
      if (selected) {
         setActivePanel('assembly');
      }
    };
    
    // Check initial
    handleStorage();
    
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  useEffect(() => {
    if (!isActive || activePanel !== 'assembly') return;

    const extusServerUrl = serverUrl.replace('artus', 'extus');
    let mounted = true;
    let mtime = 0;
    
    const checkStatus = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const res = await apiFetch(`${extusServerUrl}/status`, getAccessToken);
        if (res.ok) {
          const data = await res.json();
          if (data.mtime && data.mtime !== mtime) {
            if (mounted) {
              mtime = data.mtime;
              setExtusUrl(`${extusServerUrl}/model?t=${data.mtime}`);
            }
          }
        }
      } catch (e) {
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, getPollingDelay(MODEL_STATUS_POLL_INTERVAL_MS));
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [serverUrl, getAccessToken, isActive, activePanel]);

  useEffect(() => {
    if (!extusUrl || !isActive || activePanel !== 'assembly') return;
    let isCancelled = false;
    const loader = new GLTFLoader();

    apiFetch(extusUrl, getAccessToken)
      .then(res => res.arrayBuffer())
      .then(buffer => {
        if (isCancelled) return;
        loader.parse(buffer, '', (gltf) => {
          if (!isCancelled) setSceneGraph(gltf.scene);
        }, (err) => {
          if (!isCancelled) console.error("Error parsing assembly GLTF:", err);
        });
      })
      .catch(err => {
        if (!isCancelled) console.error("Error fetching assembly GLTF:", err);
      });

    return () => {
      isCancelled = true;
    };
  }, [extusUrl, getAccessToken, isActive, activePanel]);

  const fetchFeatures = useCallback(async () => {
    if (!shouldRunPollingRequest()) return;
    try {
      const res = await apiFetch(`${serverUrl}/features`, getAccessToken);
      const data = await res.json();
      if (res.ok) {
        setFeatures(data.features || []);
        setOperations(data.operations || []);
        setError(null);
      } else {
        setError(data.error);
        setFeatures([]);
        setOperations([]);
      }
    } catch (e) {
      setError("Failed to connect to Artus server.");
    }
  }, [serverUrl, getAccessToken]);

  useEffect(() => {
    if (!isActive) return;

    fetchFeatures();
    const interval = setInterval(fetchFeatures, getPollingDelay(PROJECT_DATA_POLL_INTERVAL_MS));
    return () => clearInterval(interval);
  }, [fetchFeatures, isActive]);

  // Auto-generate AI prompt whenever edits change
  useEffect(() => {
    const changedKeys = Object.keys(edits).filter(k => {
      const orig = features.find(f => f.name === k);
      return orig && String(orig.value) !== edits[k];
    });
    
    if (changedKeys.length > 0) {
      const autoPrompt = changedKeys.map(k => `Change ${k} to ${edits[k]}`).join('. ') + '.';
      setPrompt(autoPrompt);
    }
  }, [edits, features]);

  const handleAiModify = async () => {
    if (!prompt.trim()) return;
    setIsProcessing(true);
    setAiMessage(null);
    try {
      const res = await apiFetch(`${serverUrl}/ai_modify`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt })
      });
      const data = await res.json();
      if (data.success) {
        setAiMessage(data.message);
        setPrompt('');
        setEdits({}); // Clear local edits on success
        fetchFeatures();
      } else {
        setAiMessage(`Error: ${data.error}`);
      }
    } catch (e) {
      setAiMessage("Network error during AI request.");
    }
    setIsProcessing(false);
  };

  const handleDirectApply = async () => {
    if (Object.keys(edits).length === 0) return;
    
    try {
      const res = await apiFetch(`${serverUrl}/update_features`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: edits })
      });
      const data = await res.json();
      if (data.success) {
        setEdits({});
        setPrompt('');
        fetchFeatures();
      } else {
        setError(`Failed to apply edits: ${data.error}`);
      }
    } catch (e) {
      setError("Network error applying edits.");
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-900 p-4 overflow-hidden">
      <div className="max-w-4xl w-full mx-auto h-full flex flex-col gap-4 min-h-0">
        
        {error && (
          <div className="p-4 bg-red-950/30 border border-red-900/50 rounded-lg text-red-400 text-sm shrink-0">
            {error}
          </div>
        )}

        <div className="flex-1 flex flex-col gap-6 min-h-0">
          {/* Top: Feature Tree */}
          <div className={`flex flex-col bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-lg transition-all ${activePanel === 'variables' ? 'flex-1 min-h-0' : 'shrink-0'}`}>
            <div 
              className="flex flex-wrap items-center justify-between p-3 border-b border-slate-800 bg-slate-900/50 shrink-0 gap-2 cursor-pointer hover:bg-slate-800/80 transition-colors"
              onClick={() => setActivePanel('variables')}
            >
              <h2 className="text-lg font-bold text-slate-100 flex flex-wrap items-center gap-2">
                <span className="text-emerald-500 shrink-0">🌲</span> Parametric Variables
              </h2>
              <div onClick={e => e.stopPropagation()}>
                  <ProjectSelector isActive={isActive} />
              </div>
              <div 
                className="flex items-center gap-2 shrink-0 ml-auto"
                onClick={e => e.stopPropagation()}
              >
                <button 
                  onClick={() => setEdits({})}
                  disabled={Object.keys(edits).length === 0}
                  className="text-xs px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-slate-300 transition-colors"
                >
                  Clear
                </button>
                <button 
                  onClick={handleDirectApply}
                  disabled={Object.keys(edits).length === 0}
                  className="text-xs px-4 py-1 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed rounded text-white font-semibold transition-colors shadow"
                >
                  Apply Edits
                </button>
              </div>
            </div>
            
            {activePanel === 'variables' && (
              <div className="flex-1 overflow-y-auto p-4">
                {features.length === 0 ? (
                  <div className="text-slate-500 text-center py-8 text-sm">
                    No variables found in the current project.
                  </div>
                ) : (
                  <div className="flex flex-col font-mono text-sm">
                    <div className="flex items-center text-slate-500 mb-1 py-1 px-2">
                      <span className="w-6 shrink-0"></span>
                      <span className="flex-1 uppercase text-xs tracking-wider min-w-0 pr-2">Name</span>
                      <span className="w-4 shrink-0"></span>
                      <span className="w-20 uppercase text-xs tracking-wider shrink-0">Value</span>
                    </div>
                  {features.map((f, i) => {
                    const isLast = i === features.length - 1;
                    const currentVal = edits[f.name] !== undefined ? edits[f.name] : String(f.value);
                    const isEdited = currentVal !== String(f.value);
                    
                    return (
                      <div key={i} className={`flex items-center group py-0.5 px-2 hover:bg-slate-800/50 transition-colors ${isEdited ? 'bg-emerald-950/30' : ''}`}>
                        <div className="w-6 text-slate-600 flex-shrink-0 select-none">
                          {isLast ? '└─' : '├─'}
                        </div>
                        <div className="flex-1 text-emerald-400 font-bold truncate pr-2 min-w-0 cursor-help" title={`Type: ${f.type}${f.description ? `\nDescription: ${f.description}` : ''}`}>
                          {f.name}
                        </div>
                        <div className="w-4 text-slate-500 select-none shrink-0">=</div>
                        <div className="w-20 shrink-0">
                          <input
                            className={`w-full bg-transparent border-b outline-none ${
                              isEdited ? 'border-emerald-500 text-emerald-300 font-bold' : 'border-slate-700 text-slate-300 hover:border-slate-500'
                            }`}
                            value={currentVal}
                            onChange={e => setEdits({ ...edits, [f.name]: e.target.value })}
                          />
                        </div>
                      </div>
                    );
                  })}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Middle: Assembly Tree */}
          <div className={`flex flex-col bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-lg transition-all ${activePanel === 'assembly' ? 'flex-1 min-h-0' : 'shrink-0'}`}>
            <div 
              className="flex items-center justify-between p-3 border-b border-slate-800 bg-slate-900/50 shrink-0 cursor-pointer hover:bg-slate-800/80 transition-colors"
              onClick={() => setActivePanel('assembly')}
            >
              <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                <span className="text-sky-500">🧊</span> Assembly Tree
              </h2>
            </div>
            
            {activePanel === 'assembly' && (
              <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                {!sceneGraph ? (
                  <div className="text-slate-500 text-center py-8 text-sm">
                    Waiting for 3D model...
                  </div>
                ) : (
                  <div className="flex flex-col ml-2">
                    {sceneGraph.children.map(child => (
                      <TreeNode 
                        key={child.uuid} 
                        node={child} 
                        depth={0} 
                        selectedId={highlightedNode} 
                        onSelect={handleSelectNode} 
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Bottom: Geometric Operations */}
          <div className={`flex flex-col bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-lg transition-all ${activePanel === 'operations' ? 'flex-1 min-h-0' : 'shrink-0'}`}>
            <div 
              className="flex items-center justify-between p-3 border-b border-slate-800 bg-slate-900/50 shrink-0 cursor-pointer hover:bg-slate-800/80 transition-colors"
              onClick={() => setActivePanel('operations')}
            >
              <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                <span className="text-indigo-500">⚙️</span> Geometric Operations
              </h2>
            </div>
            
            {activePanel === 'operations' && (
              <div className="flex-1 overflow-y-auto p-4">
                {operations.length === 0 ? (
                  <div className="text-slate-500 text-center py-8 text-sm">
                    No geometric operations found.
                  </div>
                ) : (
                  <div className="flex flex-col ml-2">
                    {operations.map((op, i) => (
                      <RenderOperation 
                        key={i} 
                        node={op} 
                        depth={0} 
                        highlightedNode={highlightedNode}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Bottom: AI Input */}
        <div className="bg-slate-950 border border-emerald-900/30 rounded-xl p-4 shadow-lg shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-emerald-500">✨</span>
            <h3 className="font-semibold text-sm text-emerald-50">AI Design Modification</h3>
          </div>
          
          <div className="flex gap-3">
            <input 
              className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2 text-sm focus:outline-none focus:border-emerald-500 text-slate-100 placeholder:text-slate-600"
              placeholder="e.g. Change the length to 1000 and add a 12mm hole at the origin..."
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              onKeyDown={e => { if(e.key === 'Enter') handleAiModify() }}
              disabled={isProcessing}
            />
            <button 
              onClick={handleAiModify}
              disabled={isProcessing || !prompt.trim()}
              className="px-6 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm font-semibold text-white shadow-lg shadow-emerald-900/20 transition-all"
            >
              {isProcessing ? 'Thinking...' : 'Apply AI'}
            </button>
          </div>
          
          {aiMessage && (
            <div className={`mt-3 text-sm ${aiMessage.startsWith('Error') ? 'text-red-400' : 'text-emerald-400'}`}>
              ↳ {aiMessage}
            </div>
          )}
        </div>
        
      </div>
    </div>
  );
};
