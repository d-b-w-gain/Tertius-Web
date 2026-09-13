import * as THREE from 'three';

export interface AssemblyCandidateNode {
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

export interface AssemblyCandidateExportSummary {
  totalScannedNodes: number;
  exportedCandidateCount: number;
  excludedMeshCount: number;
  excludedGeneratedOrDefaultCount: number;
  excludedUnsupportedCount: number;
}

export interface AssemblyCandidateExport {
  exportVersion: 1;
  exportedAt: string;
  projectName: string;
  nodeCount: number;
  summary: AssemblyCandidateExportSummary;
  nodes: AssemblyCandidateNode[];
}

export interface BomSourceCall {
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

export interface BomMetadata {
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

export function isAssemblyTreeNode(node: THREE.Object3D): boolean {
  const isMesh = (node as THREE.Mesh).isMesh === true;
  const isGroup = node.type === 'Group' || node.type === 'Object3D';
  if (isMesh && node.children.length === 0) return false;
  return isMesh || isGroup;
}

export const isGeneratedOrDefaultName = (name: string) => {
  const trimmed = name.trim();
  return DEFAULT_NODE_NAMES.has(trimmed) || GENERATED_NAME_PATTERNS.some((pattern) => pattern.test(trimmed));
};

export const sanitizePathSegment = (name: string, fallback: string) => {
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

export const collectAssemblyCandidates = (roots: THREE.Object3D[], bomMetadata?: BomMetadata | null) => {
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

export const buildAssemblyCandidateExport = (sceneGraph: THREE.Object3D, projectName: string, bomMetadata?: BomMetadata | null): AssemblyCandidateExport => {
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

export const encodeAssemblyCandidateCsv = (payload: AssemblyCandidateExport) => {
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

const slugifyFilenamePart = (value: string) => {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
};

export const datedAssemblyExportFilename = (extension: 'json' | 'csv', projectName: string) => {
  const date = new Date().toISOString().slice(0, 10);
  const projectSlug = slugifyFilenamePart(projectName);
  const projectPart = projectSlug ? `${projectSlug}-` : '';
  return `assembly-tree-candidates-${projectPart}${date}.${extension}`;
};
