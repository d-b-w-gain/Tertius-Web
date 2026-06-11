import React, { useState, useEffect } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import { ProjectSelector } from '../../shared/ui/ProjectSelector';
import { GuestWorkflowNotice } from '../../shared/ui/GuestWorkflowNotice';

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

interface AssemblyCandidateNode {
  id: string;
  name: string;
  displayName: string;
  type: string;
  path: string;
  depth: number;
  parentPath: string;
  parentName: string;
  childCount: number;
  isMesh: boolean;
  isGroup: boolean;
  hasGeometry: boolean;
  hasMaterial: boolean;
  materialNames: string[];
  boundingBox: {
    min: { x: number; y: number; z: number };
    max: { x: number; y: number; z: number };
    size: { x: number; y: number; z: number };
  } | null;
  bomCandidateSuggested: boolean;
  bomCandidateReason: string;
  sourceFunction: string;
  sourceFile: string;
  sourceScope: string;
  sourceLine: number | null;
  sourceConfidence: 'exact' | 'inferred' | 'unknown';
  sourceMatchReason: string;
  sourceParameters: Record<string, unknown>;
  standardizedInputs: Record<string, unknown>;
  bomReadiness: string;
  bomMissingFields: string[];
  bomKind: string;
}

interface AssemblyCandidateExportSummary {
  totalScannedNodes: number;
  exportedCandidateCount: number;
  excludedMeshCount: number;
  excludedGeneratedOrDefaultCount: number;
  excludedUnsupportedCount: number;
}

interface AssemblyCandidateExport {
  exportVersion: 1;
  exportedAt: string;
  projectName: string;
  nodeCount: number;
  summary: AssemblyCandidateExportSummary;
  nodes: AssemblyCandidateNode[];
}

interface BomSourceCall {
  function: string;
  sourceFile: string;
  scope: string;
  line: number;
  parameters: Record<string, unknown>;
  standardInputs: Record<string, unknown>;
  bomKind: string;
  bomReadiness: string;
  bomMissingFields: string[];
}

interface BomMetadata {
  projectName: string;
  source: string;
  sourceFiles?: string[];
  calls: BomSourceCall[];
  labels: Array<{ label: string; line: number; scope: string }>;
  standardFields: string[];
}

interface SourceMatch {
  call: BomSourceCall | null;
  confidence: 'exact' | 'inferred' | 'unknown';
  reason: string;
}

const DEFAULT_NODE_NAMES = new Set(['', 'Mesh', 'Component', 'SOLID']);
const GENERATED_NAME_PATTERNS = [
  /^=>\d+(?:_\d+)?$/i,
  /^node[_\s-]?\d+$/i,
  /^mesh[_\s-]?\d+$/i,
  /^object[_\s-]?3?d?[_\s-]?\d+$/i,
  /^shape[_\s-]?\d+$/i,
  /^face[_\s-]?\d+$/i,
  /^edge[_\s-]?\d+$/i,
  /^solid(?:[_\s-]?\d+)?$/i,
  /^compound[_\s-]?\d+$/i,
  /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i,
];

const isGeneratedOrDefaultName = (name: string) => {
  const trimmed = name.trim();
  return DEFAULT_NODE_NAMES.has(trimmed) || GENERATED_NAME_PATTERNS.some((pattern) => pattern.test(trimmed));
};

const sanitizePathSegment = (name: string, fallback: string) => {
  const label = (name || fallback).trim();
  return label.replace(/\//g, '_') || fallback;
};

const getMaterialNames = (node: THREE.Object3D) => {
  const material = (node as THREE.Mesh).material;
  if (!material) return [];
  const materials = Array.isArray(material) ? material : [material];
  return materials.map((m, index) => m.name || `material_${index + 1}`);
};

const getBoundingBox = (node: THREE.Object3D) => {
  if (!(node as THREE.Mesh).isMesh && node.children.length === 0) return null;

  const box = new THREE.Box3().setFromObject(node);
  if (box.isEmpty()) return null;

  const size = new THREE.Vector3();
  box.getSize(size);
  return {
    min: { x: box.min.x, y: box.min.y, z: box.min.z },
    max: { x: box.max.x, y: box.max.y, z: box.max.z },
    size: { x: size.x, y: size.y, z: size.z },
  };
};

const getCandidateSuggestion = (node: THREE.Object3D, displayName: string) => {
  const isMesh = (node as THREE.Mesh).isMesh === true;
  const isGroup = node.type === 'Group' || node.type === 'Object3D';
  const generatedOrDefault = isGeneratedOrDefaultName(displayName);

  if (isMesh) {
    return { suggested: false, reason: 'mesh-geometry-excluded' };
  }
  if (generatedOrDefault) {
    return { suggested: false, reason: 'default-or-generated-name' };
  }
  if (isGroup && node.children.length > 0) {
    return { suggested: true, reason: 'named-group-with-children' };
  }
  return { suggested: false, reason: 'unsupported-node-type' };
};

const normalizeForMatch = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '');

const scoreSourceCall = (node: THREE.Object3D, displayName: string, path: string, call: BomSourceCall) => {
  const nodeText = `${displayName} ${path}`.toLowerCase();
  const sourceText = `${call.function} ${call.scope} ${call.bomKind}`.toLowerCase();
  const normalizedNode = normalizeForMatch(nodeText);
  const normalizedSource = normalizeForMatch(sourceText);
  let score = 0;
  const reasons: string[] = [];

  if (nodeText.includes('fastener') && call.bomKind === 'fastener_assembly') {
    score += 8;
    reasons.push('fastener node matched fastener assembly call');
  }
  if (nodeText.includes('column') && normalizedSource.includes('column')) {
    score += 6;
    reasons.push('column node matched column source scope');
  }
  if (nodeText.includes('rafter') && normalizedSource.includes('rafter')) {
    score += 6;
    reasons.push('rafter node matched rafter source scope');
  }
  if (nodeText.includes('fascia') && normalizedSource.includes('fascia')) {
    score += 6;
    reasons.push('fascia node matched fascia source scope');
  }
  if (nodeText.includes('apex') && normalizedSource.includes('apex')) {
    score += 6;
    reasons.push('apex node matched apex source function');
  }
  if (nodeText.includes('knee') && normalizedSource.includes('knee')) {
    score += 6;
    reasons.push('knee node matched knee source function');
  }
  if (nodeText.includes('base') && (normalizedSource.includes('gpb') || normalizedSource.includes('base'))) {
    score += 5;
    reasons.push('base node matched GPB/base source function');
  }
  if ((nodeText.includes('100cp') || normalizedNode.includes('cpbracket')) && normalizedSource.includes('100cp')) {
    score += 7;
    reasons.push('100CP node matched 100CP source function');
  }
  if (nodeText.includes('bracket') && call.bomKind === 'bracket') {
    score += 3;
    reasons.push('bracket node matched bracket source kind');
  }
  if (nodeText.includes('foundation') && call.bomKind === 'foundation') {
    score += 6;
    reasons.push('foundation node matched foundation source function');
  }
  if ((nodeText.includes('block') || nodeText.includes('versaloc')) && call.bomKind === 'block') {
    score += 6;
    reasons.push('block node matched block source function');
  }

  if ((node as THREE.Mesh).isMesh) {
    score = 0;
  }

  return { score, reason: reasons.join('; ') };
};

const findSourceMatch = (node: THREE.Object3D, displayName: string, path: string, bomMetadata?: BomMetadata | null): SourceMatch => {
  if (!bomMetadata?.calls?.length) {
    return { call: null, confidence: 'unknown', reason: 'no-source-metadata-loaded' };
  }

  let best: { call: BomSourceCall; score: number; reason: string } | null = null;
  for (const call of bomMetadata.calls) {
    const scored = scoreSourceCall(node, displayName, path, call);
    if (!best || scored.score > best.score) {
      best = { call, score: scored.score, reason: scored.reason };
    }
  }

  if (!best || best.score < 5) {
    return { call: null, confidence: 'unknown', reason: 'no-confident-source-match' };
  }

  return {
    call: best.call,
    confidence: 'inferred',
    reason: best.reason || 'best-effort-source-match',
  };
};

const createEmptyAssemblyExportSummary = (): AssemblyCandidateExportSummary => ({
  totalScannedNodes: 0,
  exportedCandidateCount: 0,
  excludedMeshCount: 0,
  excludedGeneratedOrDefaultCount: 0,
  excludedUnsupportedCount: 0,
});

const collectAssemblyCandidates = (roots: THREE.Object3D[], bomMetadata?: BomMetadata | null) => {
  const rows: AssemblyCandidateNode[] = [];
  const summary = createEmptyAssemblyExportSummary();

  const visit = (node: THREE.Object3D, parentPath: string, parentName: string, candidateDepth: number, siblingIndex: number) => {
    const isMesh = (node as THREE.Mesh).isMesh === true;
    const isGroup = node.type === 'Group' || node.type === 'Object3D';
    const fallbackName = isMesh ? 'Mesh' : 'Component';
    const displayName = node.name || fallbackName;
    const pathSegment = sanitizePathSegment(displayName, `${fallbackName}_${siblingIndex + 1}`);
    const path = parentPath ? `${parentPath}/${pathSegment}` : pathSegment;
    const candidate = getCandidateSuggestion(node, displayName);
    const sourceMatch = findSourceMatch(node, displayName, path, bomMetadata);
    const sourceCall = sourceMatch.call;
    const includeNode = candidate.suggested;

    summary.totalScannedNodes += 1;

    if (includeNode) {
      rows.push({
        id: node.uuid,
        name: node.name || '',
        displayName,
        type: node.type,
        path,
        depth: candidateDepth,
        parentPath,
        parentName,
        childCount: node.children.length,
        isMesh,
        isGroup,
        hasGeometry: Boolean((node as THREE.Mesh).geometry),
        hasMaterial: Boolean((node as THREE.Mesh).material),
        materialNames: getMaterialNames(node),
        boundingBox: getBoundingBox(node),
        bomCandidateSuggested: candidate.suggested,
        bomCandidateReason: candidate.reason,
        sourceFunction: sourceCall?.function || '',
        sourceFile: sourceCall?.sourceFile || '',
        sourceScope: sourceCall?.scope || '',
        sourceLine: sourceCall?.line ?? null,
        sourceConfidence: sourceMatch.confidence,
        sourceMatchReason: sourceMatch.reason,
        sourceParameters: sourceCall?.parameters || {},
        standardizedInputs: sourceCall?.standardInputs || {},
        bomReadiness: sourceCall?.bomReadiness || '',
        bomMissingFields: sourceCall?.bomMissingFields || [],
        bomKind: sourceCall?.bomKind || '',
      });

      summary.exportedCandidateCount += 1;
    } else if (isMesh) {
      summary.excludedMeshCount += 1;
    } else if (isGeneratedOrDefaultName(displayName)) {
      summary.excludedGeneratedOrDefaultCount += 1;
    } else {
      summary.excludedUnsupportedCount += 1;
    }

    const nextParentPath = includeNode ? path : parentPath;
    const nextParentName = includeNode ? displayName : parentName;
    const nextCandidateDepth = includeNode ? candidateDepth + 1 : candidateDepth;
    node.children.forEach((child, index) => visit(child, nextParentPath, nextParentName, nextCandidateDepth, index));
  };

  roots.forEach((root, index) => visit(root, '', '', 0, index));
  return { nodes: rows, summary };
};

const buildAssemblyCandidateExport = (sceneGraph: THREE.Object3D, projectName: string, bomMetadata?: BomMetadata | null): AssemblyCandidateExport => {
  const { nodes, summary } = collectAssemblyCandidates(sceneGraph.children, bomMetadata);
  return {
    exportVersion: 1,
    exportedAt: new Date().toISOString(),
    projectName,
    nodeCount: nodes.length,
    summary,
    nodes,
  };
};

const csvValue = (value: unknown) => {
  const raw = value === null || value === undefined ? '' : String(value);
  return `"${raw.replace(/"/g, '""')}"`;
};

const encodeAssemblyCandidateCsv = (payload: AssemblyCandidateExport) => {
  const headers = [
    'totalScannedNodes',
    'exportedCandidateCount',
    'excludedMeshCount',
    'excludedGeneratedOrDefaultCount',
    'excludedUnsupportedCount',
    'id',
    'name',
    'displayName',
    'type',
    'path',
    'depth',
    'parentPath',
    'parentName',
    'childCount',
    'isMesh',
    'isGroup',
    'hasGeometry',
    'hasMaterial',
    'materialNames',
    'boundingBoxMin',
    'boundingBoxMax',
    'boundingBoxSize',
    'bomCandidateSuggested',
    'bomCandidateReason',
    'sourceFunction',
    'sourceFile',
    'sourceScope',
    'sourceLine',
    'sourceConfidence',
    'sourceMatchReason',
    'sourceParameters',
    'standardizedInputs',
    'bomReadiness',
    'bomMissingFields',
    'bomKind',
  ];

  const rows = payload.nodes.map((node) => [
    payload.summary.totalScannedNodes,
    payload.summary.exportedCandidateCount,
    payload.summary.excludedMeshCount,
    payload.summary.excludedGeneratedOrDefaultCount,
    payload.summary.excludedUnsupportedCount,
    node.id,
    node.name,
    node.displayName,
    node.type,
    node.path,
    node.depth,
    node.parentPath,
    node.parentName,
    node.childCount,
    node.isMesh,
    node.isGroup,
    node.hasGeometry,
    node.hasMaterial,
    node.materialNames.join('|'),
    node.boundingBox ? `${node.boundingBox.min.x},${node.boundingBox.min.y},${node.boundingBox.min.z}` : '',
    node.boundingBox ? `${node.boundingBox.max.x},${node.boundingBox.max.y},${node.boundingBox.max.z}` : '',
    node.boundingBox ? `${node.boundingBox.size.x},${node.boundingBox.size.y},${node.boundingBox.size.z}` : '',
    node.bomCandidateSuggested,
    node.bomCandidateReason,
    node.sourceFunction,
    node.sourceFile,
    node.sourceScope,
    node.sourceLine ?? '',
    node.sourceConfidence,
    node.sourceMatchReason,
    JSON.stringify(node.sourceParameters),
    JSON.stringify(node.standardizedInputs),
    node.bomReadiness,
    node.bomMissingFields.join('|'),
    node.bomKind,
  ]);

  return [headers, ...rows].map((row) => row.map(csvValue).join(',')).join('\n');
};

const downloadTextFile = (filename: string, content: string, type: string) => {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
};

const slugifyFilenamePart = (value: string) => {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
};

const datedAssemblyExportFilename = (extension: 'json' | 'csv', projectName: string) => {
  const date = new Date().toISOString().slice(0, 10);
  const projectSlug = slugifyFilenamePart(projectName);
  const projectPart = projectSlug ? `${projectSlug}-` : '';
  return `assembly-tree-candidates-${projectPart}${date}.${extension}`;
};

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

export const FeatureTreeTab: React.FC<{ serverUrl: string }> = (props) => {
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

const AuthenticatedFeatureTreeTab: React.FC<{ serverUrl: string }> = ({ serverUrl }) => {
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
  
  const [projectName, setProjectName] = useState<string>('');
  const [extusUrl, setExtusUrl] = useState<string>('');
  const [sceneGraph, setSceneGraph] = useState<THREE.Object3D | null>(null);
  const [bomMetadata, setBomMetadata] = useState<BomMetadata | null>(null);
  
  const [highlightedNode, setHighlightedNode] = useState<string | null>(null);

  const buildExportPayload = () => {
    if (!sceneGraph) return null;
    return buildAssemblyCandidateExport(sceneGraph, projectName, bomMetadata);
  };

  const handleExportAssemblyJson = () => {
    const payload = buildExportPayload();
    if (!payload) return;
    downloadTextFile(
      datedAssemblyExportFilename('json', projectName),
      JSON.stringify(payload, null, 2),
      'application/json',
    );
  };

  const handleExportAssemblyCsv = () => {
    const payload = buildExportPayload();
    if (!payload) return;
    downloadTextFile(
      datedAssemblyExportFilename('csv', projectName),
      encodeAssemblyCandidateCsv(payload),
      'text/csv',
    );
  };

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
    const extusServerUrl = serverUrl.replace('artus', 'extus');
    let mounted = true;
    let mtime = 0;
    
    const checkStatus = async () => {
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
    const interval = setInterval(checkStatus, 3000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [serverUrl, getAccessToken]);

  useEffect(() => {
    if (!extusUrl) return;
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
  }, [extusUrl, getAccessToken]);

  const fetchFeatures = async () => {
    try {
      const res = await apiFetch(`${serverUrl}/features`, getAccessToken);
      const data = await res.json();
      if (res.ok) {
        setProjectName(data.project_name || '');
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
  };

  const fetchBomMetadata = async () => {
    try {
      const res = await apiFetch(`${serverUrl}/bom_metadata`, getAccessToken);
      const data = await res.json();
      if (res.ok) {
        setBomMetadata(data);
      } else {
        setBomMetadata(null);
      }
    } catch (e) {
      setBomMetadata(null);
    }
  };

  useEffect(() => {
    fetchFeatures();
    fetchBomMetadata();
    const interval = setInterval(() => {
      fetchFeatures();
      fetchBomMetadata();
    }, 4000);
    return () => clearInterval(interval);
  }, [serverUrl, getAccessToken]);

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
                  <ProjectSelector />
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
              className="flex items-center justify-between p-3 border-b border-slate-800 bg-slate-900/50 shrink-0 cursor-pointer hover:bg-slate-800/80 transition-colors gap-3"
              onClick={() => setActivePanel('assembly')}
            >
              <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                <span className="text-sky-500">🧊</span> Assembly Tree
              </h2>
              <div className="flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                <button
                  onClick={handleExportAssemblyJson}
                  disabled={!sceneGraph}
                  className="text-xs px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-slate-300 transition-colors"
                  title="Export the current Assembly Tree candidate nodes as JSON"
                >
                  Export JSON
                </button>
                <button
                  onClick={handleExportAssemblyCsv}
                  disabled={!sceneGraph}
                  className="text-xs px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-slate-300 transition-colors"
                  title="Export the current Assembly Tree candidate nodes as CSV"
                >
                  Export CSV
                </button>
              </div>
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
