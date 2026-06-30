import React, { useCallback, useEffect, useMemo, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import { ACTIVE_PROJECT_CHANGED_EVENT, ProjectSelector } from '../../shared/ui/ProjectSelector';
import { GuestWorkflowNotice } from '../../shared/ui/GuestWorkflowNotice';
import {
  createProjectStorage,
  type ProjectFileMetadata,
} from '../../shared/projectStorage';
import {
  MODEL_STATUS_POLL_INTERVAL_MS,
  PROJECT_DATA_POLL_INTERVAL_MS,
  getPollingDelay,
  shouldRunPollingRequest,
} from '../../shared/polling';
import { runWithInteractionSpan } from '../../../telemetry';
import {
  SCENE_NODE_APPEARANCE_STORAGE_KEY,
  SCENE_NODE_SELECTION_STORAGE_KEY,
  type SceneNodeAppearanceMap,
  createSceneNodeSelectionValue,
  getSceneNodePathKey,
  isSceneNodeSelectionMatch,
  readSceneNodeAppearanceMap,
  writeSceneNodeAppearanceMap,
} from '../../shared/sceneNodeSelection';

const AI_EDIT_FILE_LIMIT = 20;
const AI_EDIT_COMPILE_FORMAT = 'glb';
const AI_EDIT_COMPILE_QUALITY = 'sketch';

type EditableFilePointer = ProjectFileMetadata & {
  id: string;
  updated_at: string;
};

function deriveIntusServerUrl(artusServerUrl: string): string {
  const trimmed = artusServerUrl.replace(/\/+$/g, '');
  if (trimmed.endsWith('/artus')) {
    return `${trimmed.slice(0, -'/artus'.length)}/intus`;
  }
  return trimmed.replace('/api/artus', '/api/intus');
}

function hasEditableFilePointer(file: ProjectFileMetadata): file is EditableFilePointer {
  return Boolean(file.id && file.updated_at);
}

const EyeIcon: React.FC = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" />
    <circle cx="12" cy="12" r="2.5" />
  </svg>
);

const EyeOffIcon: React.FC = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9.9 5.2A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a18.4 18.4 0 0 1-3 4.1" />
    <path d="M14.1 14.1A3 3 0 0 1 9.9 9.9" />
    <path d="M2 2l20 20" />
    <path d="M6.4 6.4C3.6 8.3 2 12 2 12s3.5 7 10 7a10.5 10.5 0 0 0 5.6-1.6" />
  </svg>
);

const TransparencyIcon: React.FC<{ active: boolean }> = ({ active }) => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="8" />
    <path d="M12 4v16" />
    {active && <path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" opacity="0.45" stroke="none" />}
  </svg>
);

function isAssemblyTreeNode(node: THREE.Object3D): boolean {
  const isMesh = (node as THREE.Mesh).isMesh === true;
  const isGroup = node.type === 'Group' || node.type === 'Object3D';
  if (isMesh && node.children.length === 0) return false;
  return isMesh || isGroup;
}

// Helper component for the recursive assembly tree
const TreeNode: React.FC<{
  node: THREE.Object3D;
  root: THREE.Object3D;
  depth: number;
  selectedValue: string | null;
  appearanceByPath: SceneNodeAppearanceMap;
  onSelect: (node: THREE.Object3D, isDouble: boolean) => void;
  onToggleVisibility: (node: THREE.Object3D) => void;
  onToggleTransparency: (node: THREE.Object3D) => void;
}> = ({ node, root, depth, selectedValue, appearanceByPath, onSelect, onToggleVisibility, onToggleTransparency }) => {
  const [expanded, setExpanded] = useState(depth < 2);
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  
  const isMesh = (node as THREE.Mesh).isMesh;
  const isGroup = node.type === 'Group' || node.type === 'Object3D';
  const visibleChildren = useMemo(() => node.children.filter(isAssemblyTreeNode), [node.children]);
  const hasChildren = visibleChildren.length > 0;
  
  if (!isAssemblyTreeNode(node) || (!isMesh && !isGroup)) return null;
  
  const displayName = node.name || (isMesh ? 'Mesh' : 'Component');
  const isSelected = isSceneNodeSelectionMatch(root, node, selectedValue);
  const nodePathKey = getSceneNodePathKey(root, node);
  const appearance = appearanceByPath[nodePathKey] || {};
  const isHidden = appearance.hidden === true;
  const isTransparent = appearance.transparent === true;
  const showControls = isHovered || isFocused || isHidden || isTransparent;

  return (
    <div className="flex flex-col font-mono text-xs">
       <div 
         className={`flex w-full items-center py-0.5 px-2 cursor-pointer transition-colors ${isSelected ? 'bg-indigo-900/40 border border-indigo-500/50 rounded shadow-[inset_0_0_10px_rgba(99,102,241,0.2)]' : 'hover:bg-slate-800/50'}`}
         style={{ paddingLeft: `${depth * 16 + 8}px` }}
         onMouseEnter={() => setIsHovered(true)}
         onMouseLeave={() => setIsHovered(false)}
         onFocus={() => setIsFocused(true)}
         onBlur={() => setIsFocused(false)}
         onClick={() => onSelect(node, false)}
         onDoubleClick={() => onSelect(node, true)}
       >
          {hasChildren ? (
            <span onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }} className="w-4 mr-1 text-[10px] text-slate-500 hover:text-slate-300 focus:outline-none flex-shrink-0 flex items-center justify-center opacity-70">
              {expanded ? '▼' : '▶'}
            </span>
          ) : <span className="w-4 mr-1 inline-block" />}
          <span className={`min-w-0 flex-1 text-xs font-medium truncate select-none ${isHidden ? 'text-slate-500 line-through decoration-slate-600' : isTransparent ? 'text-slate-400' : 'text-slate-300'}`}>{displayName}</span>
          <div className={`ml-2 flex shrink-0 items-center gap-1 transition-opacity duration-150 ${showControls ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}>
            <button
              type="button"
              title={isHidden ? 'Show component and children' : 'Hide component and children'}
              aria-label={isHidden ? `Show ${displayName}` : `Hide ${displayName}`}
              className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${isHidden ? 'border-slate-600 bg-slate-900 text-slate-500 hover:text-slate-200' : 'border-slate-700 bg-slate-950 text-slate-300 hover:border-sky-500 hover:text-sky-300'}`}
              onClick={(e) => {
                e.stopPropagation();
                onToggleVisibility(node);
              }}
            >
              {isHidden ? <EyeOffIcon /> : <EyeIcon />}
            </button>
            <button
              type="button"
              title={isTransparent ? 'Make opaque' : 'Make transparent'}
              aria-label={isTransparent ? `Make ${displayName} opaque` : `Make ${displayName} transparent`}
              className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${isTransparent ? 'border-cyan-500 bg-cyan-950/50 text-cyan-300' : 'border-slate-700 bg-slate-950 text-slate-300 hover:border-cyan-500 hover:text-cyan-300'}`}
              onClick={(e) => {
                e.stopPropagation();
                onToggleTransparency(node);
              }}
            >
              <TransparencyIcon active={isTransparent} />
            </button>
          </div>
       </div>
       {expanded && hasChildren && visibleChildren.map(c => (
         <div key={c.uuid} className="flex flex-col relative">
           <div 
             className="absolute left-0 top-0 bottom-0 w-px bg-slate-800/50" 
             style={{ marginLeft: `${depth * 16 + 14}px` }}
           />
           <TreeNode
             node={c}
             root={root}
             depth={depth + 1}
             selectedValue={selectedValue}
             appearanceByPath={appearanceByPath}
             onSelect={onSelect}
             onToggleVisibility={onToggleVisibility}
             onToggleTransparency={onToggleTransparency}
           />
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
  const [activeProject, setActiveProject] = useState('');
  const [isProjectSyncPending, setIsProjectSyncPending] = useState(false);
  const [, setFileMetadata] = useState<ProjectFileMetadata[]>([]);
  
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
  const [appearanceByPath, setAppearanceByPath] = useState<SceneNodeAppearanceMap>(() => (
    readSceneNodeAppearanceMap(localStorage.getItem(SCENE_NODE_APPEARANCE_STORAGE_KEY))
  ));
  const intusServerUrl = useMemo(() => deriveIntusServerUrl(serverUrl), [serverUrl]);
  const storage = useMemo(
    () => createProjectStorage({
      authMode: 'authenticated',
      serverUrl: intusServerUrl,
      getAccessToken,
    }),
    [getAccessToken, intusServerUrl],
  );

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
     const selectionValue = createSceneNodeSelectionValue(sceneGraph, node);
     
     if (isDouble) {
        if (isSceneNodeSelectionMatch(sceneGraph, node, highlightedNode)) {
           setHighlightedNode(null);
           localStorage.removeItem(SCENE_NODE_SELECTION_STORAGE_KEY);
        } else {
           setHighlightedNode(selectionValue);
           localStorage.setItem(SCENE_NODE_SELECTION_STORAGE_KEY, selectionValue);
        }
     } else {
        setHighlightedNode(selectionValue);
        localStorage.setItem(SCENE_NODE_SELECTION_STORAGE_KEY, selectionValue);
     }
     window.dispatchEvent(new Event('storage'));
  };

  const updateAppearance = useCallback((updater: (current: SceneNodeAppearanceMap) => SceneNodeAppearanceMap) => {
    setAppearanceByPath(current => {
      const next = updater(current);
      writeSceneNodeAppearanceMap(next);
      window.dispatchEvent(new Event('storage'));
      return readSceneNodeAppearanceMap(localStorage.getItem(SCENE_NODE_APPEARANCE_STORAGE_KEY));
    });
  }, []);

  const handleToggleVisibility = useCallback((node: THREE.Object3D) => {
    const nodePathKey = getSceneNodePathKey(sceneGraph, node);
    updateAppearance(current => ({
      ...current,
      [nodePathKey]: {
        ...current[nodePathKey],
        hidden: current[nodePathKey]?.hidden !== true,
      },
    }));
  }, [sceneGraph, updateAppearance]);

  const handleToggleTransparency = useCallback((node: THREE.Object3D) => {
    const nodePathKey = getSceneNodePathKey(sceneGraph, node);
    updateAppearance(current => ({
      ...current,
      [nodePathKey]: {
        ...current[nodePathKey],
        transparent: current[nodePathKey]?.transparent !== true,
      },
    }));
  }, [sceneGraph, updateAppearance]);

  // Listen to Extus selections via LocalStorage
  useEffect(() => {
    const handleStorage = () => {
      const selected = localStorage.getItem(SCENE_NODE_SELECTION_STORAGE_KEY);
      setHighlightedNode(selected || null);
      setAppearanceByPath(readSceneNodeAppearanceMap(localStorage.getItem(SCENE_NODE_APPEARANCE_STORAGE_KEY)));
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

  const loadFeatures = useCallback(async () => {
    try {
      const res = await apiFetch(`${serverUrl}/features`, getAccessToken);
      const data = await res.json();
      if (res.ok) {
        setProjectName(data.project_name || '');
        setActiveProject(data.project_name || '');
        setIsProjectSyncPending(false);
        setFeatures(data.features || []);
        setOperations(data.operations || []);
        setError(null);
      } else {
        setActiveProject('');
        setIsProjectSyncPending(false);
        setFileMetadata([]);
        setError(data.error);
        setFeatures([]);
        setOperations([]);
      }
    } catch (e) {
      setActiveProject('');
      setIsProjectSyncPending(false);
      setFileMetadata([]);
      setError("Failed to connect to Artus server.");
      setFeatures([]);
      setOperations([]);
    }
  }, [serverUrl, getAccessToken]);

  const fetchFeatures = useCallback(async () => {
    if (!shouldRunPollingRequest()) return;
    await loadFeatures();
  }, [loadFeatures]);

  useEffect(() => {
    const handleActiveProjectChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ activeProject?: string }>).detail;
      if (!detail?.activeProject) return;
      setActiveProject(detail.activeProject);
      setIsProjectSyncPending(true);
      setFileMetadata([]);
      void loadFeatures();
    };

    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChanged);
    return () => window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChanged);
  }, [loadFeatures]);

  const fetchBomMetadata = useCallback(async () => {
    if (!shouldRunPollingRequest()) return;
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
  }, [serverUrl, getAccessToken]);

  useEffect(() => {
    fetchFeatures();
    fetchBomMetadata();
    const interval = setInterval(() => {
      fetchFeatures();
      fetchBomMetadata();
    }, getPollingDelay(PROJECT_DATA_POLL_INTERVAL_MS));
    return () => clearInterval(interval);
  }, [fetchFeatures, fetchBomMetadata]);

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

  const loadAiEditFiles = useCallback(async () => {
    if (!activeProject) {
      throw new Error('No active project is selected.');
    }

    const latestMetadata = await storage.listFileMetadata(activeProject);
    setFileMetadata(latestMetadata);

    const designFile = latestMetadata.find(file => file.filename === 'design.py');
    const remainingFiles = latestMetadata.filter(file => file.filename !== 'design.py');
    const orderedFiles = [
      ...(designFile ? [designFile] : []),
      ...remainingFiles,
    ].filter(hasEditableFilePointer);

    if (orderedFiles.length === 0) {
      throw new Error('AI edit requires authenticated project file metadata. Reload the project and try again.');
    }

    const requestFiles = orderedFiles.slice(0, AI_EDIT_FILE_LIMIT);
    const truncatedMessage = orderedFiles.length > AI_EDIT_FILE_LIMIT
      ? `AI edit included ${AI_EDIT_FILE_LIMIT} of ${orderedFiles.length} files because the backend request limit is ${AI_EDIT_FILE_LIMIT}.`
      : '';

    return {
      requestFiles,
      activeFileId: designFile && hasEditableFilePointer(designFile) ? designFile.id : requestFiles[0]?.id,
      truncatedMessage,
    };
  }, [activeProject, storage]);

  const queueCompileAfterAiEdit = useCallback(async (projectName: string, changedFiles: Array<{ filename: string; content: string }>) => {
    const designChange = changedFiles.find(file => file.filename === 'design.py');
    const code = designChange?.content ?? await storage.loadCode(projectName, 'design.py');

    if (!code) {
      throw new Error('Compile could not start because design.py could not be loaded.');
    }

    const res = await runWithInteractionSpan('compile_submit', {
      workflow: 'artus',
      export_format: AI_EDIT_COMPILE_FORMAT,
      quality: AI_EDIT_COMPILE_QUALITY,
      source: 'artus_feature_tree',
    }, () => apiFetch(`${intusServerUrl}/projects/${projectName}/compile`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code,
          export_format: AI_EDIT_COMPILE_FORMAT,
          quality: AI_EDIT_COMPILE_QUALITY,
          file: 'design.py',
        }),
      }));

    if (!res.ok) {
      let message = 'Compile could not start after AI edit.';
      try {
        const data = await res.json();
        message = data.user_message || data.short || data.error || message;
      } catch {
        // Keep the fallback message when the compile endpoint has no JSON body.
      }
      throw new Error(message);
    }
  }, [getAccessToken, intusServerUrl, storage]);

  const handleAiModify = async () => {
    if (!prompt.trim() || !activeProject || isProjectSyncPending) return;
    setIsProcessing(true);
    setAiMessage(null);
    try {
      const { requestFiles, activeFileId, truncatedMessage } = await loadAiEditFiles();

      const result = await runWithInteractionSpan('llm_file_edit_submit', {
        workflow: 'artus',
        source: 'artus_feature_tree',
        active_panel: activePanel,
      }, () => storage.applyLlmFileEdit(activeProject, {
          prompt: prompt.trim(),
          files: requestFiles.map(file => ({
            id: file.id,
            filename: file.filename,
            updated_at: file.updated_at,
          })),
          active_file_id: activeFileId,
          metadata: {
            source: 'artus_feature_tree',
            active_panel: activePanel,
            highlighted_node: highlightedNode || '',
          },
        }));

      const changedMetadata = result.files.map(file => ({
        id: file.id,
        filename: file.filename,
        updated_at: file.updated_at,
      }));

      setFileMetadata(prev =>
        prev.map(existing => changedMetadata.find(file => file.id === existing.id) || existing)
      );

      const summaries = result.files
        .map(file => file.summary)
        .filter(Boolean)
        .join(' ');
      const successMessage = summaries
        ? `AI updated ${result.files.length} file(s). ${summaries}`
        : `AI updated ${result.files.length} file(s).`;

      let compileMessage = 'Compile queued for the updated design.';
      try {
        await queueCompileAfterAiEdit(activeProject, result.files);
      } catch (compileError) {
        const message = compileError instanceof Error ? compileError.message : 'Compile could not start after AI edit.';
        compileMessage = `Compile warning: ${message}`;
      }

      setAiMessage([truncatedMessage, successMessage, compileMessage].filter(Boolean).join(' '));
      setPrompt('');
      setEdits({});
      await loadFeatures();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'AI file edit failed.';
      setAiMessage(`Error: ${message}`);

      if (message.includes('Files changed while AI edit was running')) {
        try {
          await loadAiEditFiles();
          await loadFeatures();
        } catch {
          // The original conflict message is the useful user-facing error.
        }
      }
    } finally {
      setIsProcessing(false);
    }
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
        await loadFeatures();
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
                    {sceneGraph.children.filter(isAssemblyTreeNode).map(child => (
                      <TreeNode 
                        key={child.uuid} 
                        node={child} 
                        root={sceneGraph}
                        depth={0} 
                        selectedValue={highlightedNode}
                        appearanceByPath={appearanceByPath}
                        onSelect={handleSelectNode} 
                        onToggleVisibility={handleToggleVisibility}
                        onToggleTransparency={handleToggleTransparency}
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
              disabled={isProcessing || !prompt.trim() || !activeProject || isProjectSyncPending}
              title={!activeProject || isProjectSyncPending ? 'Select or load a project before using AI edit' : undefined}
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
