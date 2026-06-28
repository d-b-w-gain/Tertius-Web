import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import { perfLog } from '../../../observability/performance';
import { getPollingDelay, MODEL_STATUS_POLL_INTERVAL_MS, shouldRunPollingRequest } from '../../shared/polling';
import { createProjectStorage } from '../../shared/projectStorage';

type SubTab = 'bom' | 'suppliers' | 'review';
type CoverageState = 'valid' | 'incomplete' | 'missing' | 'mesh';
type PackageType = 'each' | 'pack' | 'box' | 'stock_length' | 'roll' | 'reel' | 'bulk_volume' | 'bulk_mass';
export type BomArtifactState = 'missing_manifest' | 'stale_manifest' | 'diagnostic_only' | 'scopes_only' | 'ready';

const INITIAL_VISIBLE_ROWS = 200;
const MANIFEST_POLL_INTERVAL_MS = 15_000;
const SCENE_IDLE_CHECK_MS = 250;
const PREVIEW_PIXEL_RATIO = 1;
const DIMMED_SELECTION_OPACITY = 0.3;
const SELECTION_EMISSIVE = 0xf59e0b;
const SELECTION_UPDATE_CHUNK_SIZE = 350;
const SELECTION_LAYER = 1;
const ENABLE_PROCUREMENT_3D_SELECTION = true;
const PACKAGE_TYPES: PackageType[] = ['each', 'box', 'pack', 'stock_length', 'roll', 'reel', 'bulk_volume', 'bulk_mass'];
const BOM_RECOVERY_POLL_MS = 2_000;
const BOM_RECOVERY_MAX_POLLS = 90;
const PROCUREMENT_SYNC_WORK_LOG_MS = 50;
const PROCUREMENT_SYNC_WORK_WARN_MS = 200;
const BOM_STARTER_SNIPPET = `from tertius_bom import bom_scope, bom_component, requirement

with bom_scope("Portal", id="portal"):
    column = make_column(...)
    bom_component(
        column,
        id="portal.column.left",
        role="Column",
        requirements=[
            requirement(
                part_number="C10019",
                quantity=1,
                unit="each",
                dimensions={"length_mm": 2400},
            )
        ],
    )
`;

const BOM_METADATA_EDIT_PROMPT = `Add explicit tertius_bom procurement metadata to the CAD design.

Required outcome:
- Import bom_scope, bom_component, and requirement from tertius_bom.
- Preserve the existing geometry and visual output.
- Wrap meaningful top-level collections in bom_scope calls, using stable ids and human labels from the design domain.
- Bind visible build components, not faces/meshes, with bom_component calls.
- Add requirement entries only for real end items. Do not invent assembly rows from GLB labels, function names, or generic keywords.
- Where the design already contains a part number, material, length, finish, grade, or standard, carry that value into the requirement.
- Where a required commercial identity is unknown, use a clear placeholder such as TODO_PART_NUMBER and keep the quantity/unit/dimensions explicit.
- Fasteners must remain separate bolt/nut/screw requirements when they are separate procurement items.
- Bulk materials may use units such as L, kg, m3, or each as appropriate.

The goal is to make compile emit a bom_manifest.json artifact with scopes, components, requirements, and diagnostics that can be visually verified in Procurement.`;

interface ManifestScope {
  id: string;
  label: string;
  parent_id: string | null;
  path?: string | null;
  source?: string | null;
  source_file?: string | null;
  source_line?: number | null;
}

interface ManifestComponent {
  id: string;
  scope_id: string | null;
  assembly_id?: string | null;
  label: string;
  role: string;
  visual_node_ids: string[];
  purchasable_kit?: boolean;
  path?: string | null;
  source_trace?: Record<string, unknown> | null;
  source_file?: string | null;
  source_line?: number | null;
}

export interface ManifestRequirement {
  id: string;
  component_id: string;
  scope_id?: string | null;
  part_number?: string | null;
  quantity?: number | string | null;
  rolled_up_quantity?: number | string | null;
  quantity_source?: string | null;
  quantity_confidence?: string | null;
  orderable?: boolean | null;
  part_number_placeholder?: boolean | null;
  visual_instance_count?: number | string | null;
  assembly_id?: string | null;
  unit?: string | null;
  dimensions?: Record<string, unknown>;
  material?: string | null;
  finish?: string | null;
  grade?: string | null;
  standard?: string | null;
  package?: Record<string, unknown> | null;
  source_trace?: Record<string, unknown> | null;
  resolution_trace?: Record<string, unknown> | null;
  count_trace?: Record<string, unknown> | null;
  source_file?: string | null;
  source_line?: number | null;
}

interface ManifestDiagnostic {
  code: string;
  severity: 'error' | 'warning' | 'info' | string;
  message: string;
  component_id?: string;
  requirement_id?: string;
  source_file?: string | null;
  source_line?: number | null;
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
  calls?: BomSourceCall[];
}

interface FeatureValue {
  name: string;
  value: string | number | boolean;
}

export interface BomManifest {
  version: number;
  source_snapshot_hash: string;
  scopes: ManifestScope[];
  components: ManifestComponent[];
  requirements: ManifestRequirement[];
  diagnostics: ManifestDiagnostic[];
  visual_path_map?: Record<string, unknown>;
}

export interface ManifestCounts {
  scopes: number;
  components: number;
  requirements: number;
  diagnostics: number;
}

export interface ManifestEnvelope {
  manifest: BomManifest;
  manifest_artifact_id: string;
  manifest_compile_job_id: string | null;
  model_artifact_id: string | null;
  model_compile_job_id: string | null;
  matches_model: boolean;
  is_verified_for_model?: boolean;
  artifact_state?: BomArtifactState;
  manifest_counts?: ManifestCounts;
  mtime: number;
}

interface ProcurementAnalysisArtifact {
  version?: number;
  source?: string;
  source_snapshot_hash?: string;
  assemblies?: Array<Record<string, unknown>>;
  components?: Array<Record<string, unknown>>;
  requirements?: Array<Record<string, unknown>>;
  diagnostics?: ManifestDiagnostic[];
  visual_path_map?: Record<string, unknown>;
}

export interface SupplierPricing {
  section: string;
  package_type: PackageType;
  stock_lengths: number[];
  package_quantity: number;
  package_unit: string;
  price_per_package: number;
  price_per_unit: number;
  notes: string;
}

interface Supplier {
  id: string;
  name: string;
  contact: string;
  phone: string;
  email: string;
  notes: string;
  pricing: SupplierPricing[];
}

export interface GroupedBomLine {
  key: string;
  displayName: string;
  partNumber: string;
  quantity: number;
  unit: string;
  dimensions: Record<string, unknown>;
  material: string;
  finish: string;
  grade: string;
  standard: string;
  componentIds: string[];
  visualNodeIds: string[];
  requirements: ManifestRequirement[];
  status: CoverageState;
}

interface ManifestIndex {
  componentsById: Map<string, ManifestComponent>;
  scopesById: Map<string, ManifestScope>;
  resolveScopeChain: (scopeId: string | null | undefined) => string[];
}

interface ScopeStats {
  allItemCount: number;
  componentCountByScopeId: Map<string, number>;
  itemCountByScopeId: Map<string, number>;
}

interface PricedLine {
  supplier_id: string;
  package_type: PackageType;
  buy_quantity: number;
  purchase_label: string;
  waste_quantity: number;
  waste_label: string;
  unit_cost: number;
  total_cost: number;
}

interface ScopeOption {
  id: string;
  label: string;
  depth: number;
  itemCount: number;
  componentCount: number;
}

interface ComponentCoverage {
  component: ManifestComponent;
  requirements: ManifestRequirement[];
  state: CoverageState;
  message: string;
}

const randomId = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `id-${Math.random().toString(36).slice(2)}`;
};

const money = (value: number) => `$${value.toFixed(2)}`;

const readJsonState = <T,>(key: string, fallback: T, normalize?: (value: unknown) => T): T => {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '') as unknown;
    return normalize ? normalize(parsed) : parsed as T;
  } catch {
    return fallback;
  }
};

const writeJsonState = (key: string, value: unknown) => {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Local storage is best-effort UI persistence.
  }
};

const asRecord = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
);

const asString = (value: unknown) => (typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value));

const asNumber = (value: unknown, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
};

const asQuantityValue = (value: unknown): number | string | null => (
  typeof value === 'number' || typeof value === 'string' ? value : null
);

const asNullableString = (value: unknown) => {
  const text = asString(value).trim();
  return text || null;
};

const hasList = (record: Record<string, unknown>, key: string) => Array.isArray(record[key]);

const isProcurementAnalysisArtifact = (value: unknown): value is ProcurementAnalysisArtifact => {
  const record = asRecord(value);
  return hasList(record, 'assemblies') && hasList(record, 'requirements') && !hasList(record, 'scopes');
};

const objectList = (value: unknown): Array<Record<string, unknown>> => (
  Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : []
);

const readableScopeLabel = (value: unknown) => {
  const text = asString(value).trim().replace(/_/g, ' ');
  const cleaned = text.replace(/\s+[A-Z]{1,6}\d{2}(?:-[A-Z0-9]+)+$/i, '').trim();
  return cleaned || text;
};

const isComponentLabelOnlyRequirement = (requirement: Record<string, unknown>) => {
  const dimensions = asRecord(requirement.dimensions);
  return !asString(requirement.part_number).trim() && Boolean(asString(dimensions.component_label).trim());
};

export const normalizeProcurementManifest = (rawManifest: BomManifest | ProcurementAnalysisArtifact | null | undefined): BomManifest | null => {
  if (!rawManifest) return null;
  if (!isProcurementAnalysisArtifact(rawManifest)) {
    const manifest = rawManifest as BomManifest;
    return {
      ...manifest,
      scopes: (manifest.scopes || []).map((scope) => ({
        ...scope,
        label: readableScopeLabel(scope.label || scope.id),
      })),
      requirements: (manifest.requirements || []).filter((requirement) => (
        !isComponentLabelOnlyRequirement(requirement as unknown as Record<string, unknown>)
      )),
    };
  }

  const usedComponentIds = new Set<string>();
  const scopes: ManifestScope[] = objectList(rawManifest.assemblies).map((assembly, index) => {
    const id = asString(assembly.id).trim() || `assembly-${index + 1}`;
    return {
      id,
      label: readableScopeLabel(assembly.label || id),
      parent_id: asNullableString(assembly.parent_id),
      path: asNullableString(assembly.path),
      source: asNullableString(assembly.source),
      source_file: asNullableString(assembly.source_file),
      source_line: assembly.source_line === null || assembly.source_line === undefined ? null : asNumber(assembly.source_line, 0),
    };
  });

  const components: ManifestComponent[] = objectList(rawManifest.components).map((component, index) => {
    const id = asString(component.id).trim() || `component-${index + 1}`;
    usedComponentIds.add(id);
    const sourceTrace = asRecord(component.source_trace);
    const role = asString(component.role).trim() || asString(sourceTrace.function).trim() || 'component';
    return {
      id,
      scope_id: asNullableString(component.scope_id ?? component.assembly_id),
      assembly_id: asNullableString(component.assembly_id),
      label: asString(component.label).trim() || id,
      role,
      visual_node_ids: Array.isArray(component.visual_node_ids) ? component.visual_node_ids.map(asString).filter(Boolean) : [],
      purchasable_kit: Boolean(component.purchasable_kit),
      path: asNullableString(component.path),
      source_trace: Object.keys(sourceTrace).length ? sourceTrace : null,
      source_file: asNullableString(component.source_file ?? sourceTrace.source_file),
      source_line: component.source_line === null || (component.source_line === undefined && sourceTrace.source_line === undefined)
        ? null
        : asNumber(component.source_line ?? sourceTrace.source_line, 0),
    };
  });

  const requirements: ManifestRequirement[] = objectList(rawManifest.requirements).filter((requirement) => (
    !isComponentLabelOnlyRequirement(requirement)
  )).map((requirement, index) => {
    const componentId = asString(requirement.component_id).trim() || `requirement-component-${index + 1}`;
    if (!usedComponentIds.has(componentId)) {
      usedComponentIds.add(componentId);
      components.push({
        id: componentId,
        scope_id: asNullableString(requirement.scope_id ?? requirement.assembly_id),
        assembly_id: asNullableString(requirement.assembly_id),
        label: componentId,
        role: 'component',
        visual_node_ids: [],
      });
    }
    const sourceTrace = asRecord(requirement.source_trace);
    return {
      id: asString(requirement.id).trim() || `${componentId}.requirement-${index + 1}`,
      component_id: componentId,
      scope_id: asNullableString(requirement.scope_id ?? requirement.assembly_id),
      assembly_id: asNullableString(requirement.assembly_id),
      part_number: asNullableString(requirement.part_number),
      quantity: asQuantityValue(requirement.quantity),
      rolled_up_quantity: asQuantityValue(requirement.rolled_up_quantity),
      quantity_source: asNullableString(requirement.quantity_source),
      quantity_confidence: asNullableString(requirement.quantity_confidence),
      orderable: typeof requirement.orderable === 'boolean' ? requirement.orderable : null,
      part_number_placeholder: typeof requirement.part_number_placeholder === 'boolean' ? requirement.part_number_placeholder : null,
      visual_instance_count: requirement.visual_instance_count as ManifestRequirement['visual_instance_count'],
      unit: asString(requirement.unit).trim() || 'each',
      dimensions: asRecord(requirement.dimensions),
      material: asNullableString(requirement.material),
      finish: asNullableString(requirement.finish),
      grade: asNullableString(requirement.grade),
      standard: asNullableString(requirement.standard),
      package: Object.keys(asRecord(requirement.package)).length ? asRecord(requirement.package) : null,
      source_trace: Object.keys(sourceTrace).length ? sourceTrace : null,
      resolution_trace: Object.keys(asRecord(requirement.resolution_trace)).length ? asRecord(requirement.resolution_trace) : null,
      count_trace: Object.keys(asRecord(requirement.count_trace)).length ? asRecord(requirement.count_trace) : null,
      source_file: asNullableString(requirement.source_file ?? sourceTrace.source_file),
      source_line: requirement.source_line === null || (requirement.source_line === undefined && sourceTrace.source_line === undefined)
        ? null
        : asNumber(requirement.source_line ?? sourceTrace.source_line, 0),
    };
  });

  return {
    version: rawManifest.version || 1,
    source_snapshot_hash: rawManifest.source_snapshot_hash || '',
    scopes,
    components,
    requirements,
    diagnostics: Array.isArray(rawManifest.diagnostics) ? rawManifest.diagnostics : [],
    visual_path_map: rawManifest.visual_path_map || {},
  };
};

export const normalizeManifestEnvelope = (envelope: ManifestEnvelope | null): ManifestEnvelope | null => {
  if (!envelope) return null;
  const manifest = normalizeProcurementManifest(envelope.manifest as BomManifest | ProcurementAnalysisArtifact);
  if (!manifest) return envelope;
  return {
    ...envelope,
    manifest,
    manifest_counts: manifestCounts(manifest),
  };
};

const normalizePricing = (value: unknown): SupplierPricing => {
  const record = asRecord(value);
  const oldStockType = asString(record.stock_type);
  const packageType = PACKAGE_TYPES.includes(record.package_type as PackageType)
    ? record.package_type as PackageType
    : oldStockType === 'reel'
      ? 'reel'
      : oldStockType === 'stick'
        ? 'stock_length'
        : 'each';
  const stockLengthsValue = Array.isArray(record.stock_lengths) ? record.stock_lengths : [];
  return {
    section: asString(record.section),
    package_type: packageType,
    stock_lengths: stockLengthsValue.map((item) => asNumber(item)).filter((item) => item > 0),
    package_quantity: Math.max(0, asNumber(record.package_quantity, 0)),
    package_unit: asString(record.package_unit || 'each'),
    price_per_package: Math.max(0, asNumber(record.price_per_package, 0)),
    price_per_unit: Math.max(0, asNumber(record.price_per_unit ?? record.price_per_m, 0)),
    notes: asString(record.notes),
  };
};

const normalizeSuppliers = (value: unknown): Supplier[] => {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const record = asRecord(item);
    const pricingValue = Array.isArray(record.pricing) ? record.pricing : [];
    return {
      id: asString(record.id) || randomId(),
      name: asString(record.name),
      contact: asString(record.contact),
      phone: asString(record.phone),
      email: asString(record.email),
      notes: asString(record.notes),
      pricing: pricingValue.map(normalizePricing),
    };
  });
};

const newSupplier = (): Supplier => ({
  id: randomId(),
  name: '',
  contact: '',
  phone: '',
  email: '',
  notes: '',
  pricing: [],
});

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

const slugId = (value: string, fallback = 'item') => {
  const slug = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug || fallback;
};

const uniqueManifestId = (base: string, used: Set<string>) => {
  let candidate = base;
  let index = 2;
  while (used.has(candidate)) {
    candidate = `${base}-${index}`;
    index += 1;
  }
  used.add(candidate);
  return candidate;
};

const isNamedGroup = (node: THREE.Object3D) => {
  const isGroup = node.type === 'Group' || node.type === 'Object3D';
  const name = node.name || '';
  return isGroup && node.children.length > 0 && !isGeneratedOrDefaultName(name);
};

const hasMeshDescendant = (node: THREE.Object3D): boolean => {
  if ((node as THREE.Mesh).isMesh) return true;
  return node.children.some(hasMeshDescendant);
};

const hasNamedGroupChildWithMeshes = (node: THREE.Object3D): boolean => (
  node.children.some((child) => (
    (isNamedGroup(child) && hasMeshDescendant(child))
    || hasNamedGroupChildWithMeshes(child)
  ))
);

const objectPath = (ancestors: THREE.Object3D[], node: THREE.Object3D) => (
  [...ancestors, node]
    .map((item, index) => (item.name || `${item.type || 'node'}_${index + 1}`).replace(/\//g, '_'))
    .filter(Boolean)
    .join('/')
);

const normalizeForMatch = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '');

const scoreSourceCall = (displayName: string, path: string, call: BomSourceCall) => {
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

  return { score, reason: reasons.join('; ') };
};

const findSourceCall = (displayName: string, path: string, metadata: BomMetadata | null) => {
  let best: { call: BomSourceCall; score: number; reason: string } | null = null;
  for (const call of metadata?.calls || []) {
    const scored = scoreSourceCall(displayName, path, call);
    if (!best || scored.score > best.score) best = { call, score: scored.score, reason: scored.reason };
  }
  return best && best.score >= 5 ? best : null;
};

const resolveCompactValue = (value: unknown, featureValues: Map<string, string | number | boolean>): unknown => {
  const record = asRecord(value);
  if (record.kind === 'literal') return record.value;
  if (record.kind === 'reference') {
    const name = asString(record.name);
    return featureValues.has(name) ? featureValues.get(name) : name;
  }
  if (record.kind === 'expression') return asString(record.source);
  return value;
};

export const deriveAssemblyTreeManifest = (
  root: THREE.Object3D,
  baseManifest: BomManifest | null,
  bomMetadata: BomMetadata | null,
  features: FeatureValue[],
): BomManifest | null => {
  const featureValues = new Map(features.map((feature) => [feature.name, feature.value]));
  const usedIds = new Set<string>();
  const scopes: ManifestScope[] = [];
  const components: ManifestComponent[] = [];
  const requirements: ManifestRequirement[] = [];
  const diagnostics: ManifestDiagnostic[] = [...(baseManifest?.diagnostics || [])];
  const scopeByObject = new Map<THREE.Object3D, ManifestScope>();
  const componentObjects: Array<{ node: THREE.Object3D; ancestors: THREE.Object3D[] }> = [];

  const visit = (node: THREE.Object3D, ancestors: THREE.Object3D[]) => {
    if (isNamedGroup(node) && hasMeshDescendant(node)) {
      if (hasNamedGroupChildWithMeshes(node)) {
        const path = objectPath(ancestors, node);
        const id = uniqueManifestId(slugId(path, 'scope'), usedIds);
        const parentScope = [...ancestors].reverse().map((ancestor) => scopeByObject.get(ancestor)).find(Boolean) || null;
        const scope = {
          id,
          label: node.name || id,
          parent_id: parentScope?.id || null,
          source_file: null,
          source_line: null,
        };
        scopes.push(scope);
        scopeByObject.set(node, scope);
      } else {
        componentObjects.push({ node, ancestors });
      }
    }
    node.children.forEach((child) => visit(child, [...ancestors, node]));
  };

  root.children.forEach((child) => visit(child, []));

  for (const { node, ancestors } of componentObjects) {
    const parentScope = [...ancestors].reverse().map((ancestor) => scopeByObject.get(ancestor)).find(Boolean) || null;
    const path = objectPath(ancestors, node);
    const sourceMatch = findSourceCall(node.name || '', path, bomMetadata);
    const sourceCall = sourceMatch?.call || null;
    const componentId = uniqueManifestId(slugId(path, 'component'), usedIds);
    components.push({
      id: componentId,
      scope_id: parentScope?.id || null,
      label: node.name || componentId,
      role: sourceCall?.bomKind || 'component',
      visual_node_ids: [node.uuid],
      source_file: sourceCall?.sourceFile || null,
      source_line: sourceCall?.line ?? null,
    });

    const standardInputs = sourceCall?.standardInputs || {};
    const partNumber = resolveCompactValue(standardInputs.part_number ?? standardInputs.product_key, featureValues);
    const resolvedPartNumber = asString(partNumber).trim();
    const lengthMm = resolveCompactValue(standardInputs.length_mm, featureValues);
    const dimensions: Record<string, unknown> = {};
    if (lengthMm !== undefined && lengthMm !== '') dimensions.length_mm = lengthMm;

    for (const key of ['width_mm', 'height_mm', 'thickness_mm', 'diameter_mm', 'grip_length_mm']) {
      const resolved = resolveCompactValue(standardInputs[key], featureValues);
      if (resolved !== undefined && resolved !== '') dimensions[key] = resolved;
    }
    if (!resolvedPartNumber) dimensions.component_label = node.name || componentId;

    requirements.push({
      id: `${componentId}.requirement`,
      component_id: componentId,
      scope_id: parentScope?.id || null,
      part_number: resolvedPartNumber || null,
      quantity: 1,
      rolled_up_quantity: 1,
      quantity_source: 'visual_instances',
      quantity_confidence: 'verified',
      orderable: true,
      unit: asString(resolveCompactValue(standardInputs.unit, featureValues) || 'each'),
      dimensions,
      material: asString(resolveCompactValue(standardInputs.material, featureValues)) || null,
      finish: asString(resolveCompactValue(standardInputs.finish, featureValues)) || null,
      grade: asString(resolveCompactValue(standardInputs.grade, featureValues)) || null,
      standard: asString(resolveCompactValue(standardInputs.standard, featureValues)) || null,
      source_file: sourceCall?.sourceFile || null,
      source_line: sourceCall?.line ?? null,
    });

    if (!sourceCall) {
      diagnostics.push({
        code: 'assembly_tree_no_source_match',
        severity: 'warning',
        message: `${node.name || componentId} was inferred from the GLTF tree, but no matching design.py source call was found.`,
        component_id: componentId,
      });
    } else if (sourceCall.bomReadiness !== 'ok') {
      diagnostics.push({
        code: 'assembly_tree_incomplete_requirement',
        severity: 'warning',
        message: `${node.name || componentId} has inferred procurement metadata but is missing ${sourceCall.bomMissingFields.join(', ') || 'some'} fields.`,
        component_id: componentId,
        source_file: sourceCall.sourceFile,
        source_line: sourceCall.line,
      });
    }
  }

  if (!components.length && !requirements.length) return null;

  return {
    version: baseManifest?.version || 1,
    source_snapshot_hash: baseManifest?.source_snapshot_hash || '',
    scopes,
    components,
    requirements,
    diagnostics: [
      ...diagnostics,
      {
        code: 'assembly_tree_inferred_manifest',
        severity: 'info',
        message: 'Procurement derived draft components and requirements from the GLTF assembly tree and Artus design.py metadata.',
      },
    ],
    visual_path_map: baseManifest?.visual_path_map || {},
  };
};

export const manifestCounts = (manifest: BomManifest | null | undefined): ManifestCounts => ({
  scopes: manifest?.scopes?.length || 0,
  components: manifest?.components?.length || 0,
  requirements: manifest?.requirements?.length || 0,
  diagnostics: manifest?.diagnostics?.length || 0,
});

export const resolveBomArtifactState = (envelope: ManifestEnvelope | null): BomArtifactState => {
  if (!envelope) return 'missing_manifest';
  if (envelope.artifact_state) return envelope.artifact_state;
  if (!envelope.matches_model) return 'stale_manifest';

  const counts = manifestCounts(envelope.manifest);
  if (counts.requirements > 0) return 'ready';
  if (counts.scopes > 0 || counts.components > 0) return 'scopes_only';
  return 'diagnostic_only';
};

const artifactStateTitle = (state: BomArtifactState) => {
  if (state === 'ready') return 'Verified BoM artifact';
  if (state === 'scopes_only') return 'Assembly views only';
  if (state === 'diagnostic_only') return 'No usable BoM artifact yet';
  if (state === 'stale_manifest') return 'BoM artifact does not match the model';
  return 'No BoM manifest artifact';
};

const artifactStateMessage = (state: BomArtifactState) => {
  if (state === 'ready') return 'The current model and BoM manifest came from the same compile job.';
  if (state === 'scopes_only') return 'This compile produced selectable assembly views, but no procurement requirements have been declared for them yet.';
  if (state === 'diagnostic_only') return 'This compile produced diagnostics only. Procurement will not invent rows from mesh names, function names, or GLB labels.';
  if (state === 'stale_manifest') return 'The latest model and latest BoM manifest came from different compile jobs. Recompile before treating Procurement as verified.';
  return 'A 3D model can exist without a matching BoM manifest. Compile a design that emits tertius_bom metadata to create one.';
};

const dimensionsKey = (dimensions: Record<string, unknown>) => (
  Object.entries(dimensions)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '')
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${String(value)}`)
    .join('|')
);

export const canonicalRequirementKey = (requirement: ManifestRequirement) => [
  asString(requirement.part_number).trim(),
  asString(requirement.unit || 'each').trim(),
  dimensionsKey(requirement.dimensions || {}),
  asString(requirement.material).trim(),
  asString(requirement.finish).trim(),
  asString(requirement.grade).trim(),
  asString(requirement.standard).trim(),
].join('::');

const requirementQuantity = (requirement: ManifestRequirement, selectedScopeId = '__all__') => {
  if (requirement.orderable === false) return 0;
  const value = selectedScopeId === '__all__'
    ? requirement.rolled_up_quantity ?? requirement.quantity
    : requirement.quantity;
  const quantity = asNumber(value, 0);
  return quantity > 0 ? quantity : 0;
};

const requirementComplete = (requirement: ManifestRequirement) => (
  Boolean(asString(requirement.part_number).trim())
  && requirement.part_number_placeholder !== true
  && requirementQuantity(requirement) > 0
  && Boolean(asString(requirement.unit || 'each').trim())
);

const displayAlias = (partNumber: string, dimensions: Record<string, unknown>) => {
  const lengthMm = asNumber(dimensions.length_mm, 0);
  if (!partNumber || lengthMm <= 0) return partNumber || '(missing part number)';
  const lengthCode = lengthMm / 100;
  const formatted = Number.isInteger(lengthCode) ? String(lengthCode) : lengthCode.toFixed(1).replace(/\.0$/, '');
  return `${partNumber}x${formatted}`;
};

const scopeDepth = (scope: ManifestScope, byId: Map<string, ManifestScope>) => {
  let depth = 0;
  let parentId = scope.parent_id;
  while (parentId) {
    depth += 1;
    parentId = byId.get(parentId)?.parent_id || null;
  }
  return depth;
};

const measureProcurementWork = <T,>(
  event: string,
  details: Record<string, unknown>,
  work: () => T,
): T => {
  const startedAt = performance.now();
  const result = work();
  const durationMs = Math.round(performance.now() - startedAt);
  if (durationMs >= PROCUREMENT_SYNC_WORK_LOG_MS) {
    perfLog(
      'Procurement',
      event,
      { ...details, durationMs },
      durationMs >= PROCUREMENT_SYNC_WORK_WARN_MS ? 'warn' : 'info',
    );
  }
  return result;
};

const createManifestIndex = (manifest: BomManifest | null): ManifestIndex => {
  const componentsById = new Map((manifest?.components || []).map((component) => [component.id, component]));
  const scopesById = new Map((manifest?.scopes || []).map((scope) => [scope.id, scope]));
  const scopeChainCache = new Map<string, string[]>();

  const resolveScopeChain = (scopeId: string | null | undefined): string[] => {
    if (!scopeId) return [];
    const cached = scopeChainCache.get(scopeId);
    if (cached) return cached;

    const chain: string[] = [];
    const visited = new Set<string>();
    let current: string | null = scopeId;
    while (current && !visited.has(current)) {
      chain.push(current);
      visited.add(current);
      current = scopesById.get(current)?.parent_id || null;
    }
    scopeChainCache.set(scopeId, chain);
    return chain;
  };

  return { componentsById, scopesById, resolveScopeChain };
};

const createScopeStats = (manifest: BomManifest | null, index: ManifestIndex): ScopeStats => {
  const componentCountByScopeId = new Map<string, number>();
  const itemKeysByScopeId = new Map<string, Set<string>>();
  const allItemKeys = new Set<string>();

  for (const scope of manifest?.scopes || []) {
    componentCountByScopeId.set(scope.id, 0);
    itemKeysByScopeId.set(scope.id, new Set<string>());
  }

  for (const component of manifest?.components || []) {
    for (const scopeId of index.resolveScopeChain(component.scope_id)) {
      componentCountByScopeId.set(scopeId, (componentCountByScopeId.get(scopeId) || 0) + 1);
    }
  }

  for (const requirement of manifest?.requirements || []) {
    const component = index.componentsById.get(requirement.component_id);
    const key = canonicalRequirementKey(requirement);
    allItemKeys.add(key);
    for (const scopeId of index.resolveScopeChain(component?.scope_id ?? requirement.scope_id ?? null)) {
      const itemKeys = itemKeysByScopeId.get(scopeId);
      if (itemKeys) itemKeys.add(key);
    }
  }

  return {
    allItemCount: allItemKeys.size,
    componentCountByScopeId,
    itemCountByScopeId: new Map(
      [...itemKeysByScopeId.entries()].map(([scopeId, itemKeys]) => [scopeId, itemKeys.size]),
    ),
  };
};

export const groupManifestRequirements = (
  manifest: BomManifest | null,
  selectedScopeId: string,
  index = createManifestIndex(manifest),
): GroupedBomLine[] => {
  if (!manifest) return [];
  const groups = new Map<string, GroupedBomLine>();

  for (const requirement of manifest.requirements) {
    const component = index.componentsById.get(requirement.component_id);
    const scopeId = component?.scope_id ?? requirement.scope_id ?? null;
    if (selectedScopeId !== '__all__' && !index.resolveScopeChain(scopeId).includes(selectedScopeId)) continue;

    const key = canonicalRequirementKey(requirement);
    const partNumber = asString(requirement.part_number).trim();
    const unit = asString(requirement.unit || 'each').trim() || 'each';
    const dimensions = requirement.dimensions || {};
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        key,
        displayName: displayAlias(partNumber, dimensions),
        partNumber,
        quantity: 0,
        unit,
        dimensions,
        material: asString(requirement.material),
        finish: asString(requirement.finish),
        grade: asString(requirement.grade),
        standard: asString(requirement.standard),
        componentIds: [],
        visualNodeIds: [],
        requirements: [],
        status: 'valid',
      });
    }
    const group = groups.get(key);
    if (!group) continue;
    group.quantity += requirementQuantity(requirement, selectedScopeId);
    group.requirements.push(requirement);
    if (!requirementComplete(requirement)) group.status = 'incomplete';
    if (component) {
      group.componentIds.push(component.id);
      group.visualNodeIds.push(...component.visual_node_ids);
    }
  }

  return [...groups.values()]
    .map((line) => ({
      ...line,
      quantity: Number(line.quantity.toFixed(3)),
      componentIds: [...new Set(line.componentIds)],
      visualNodeIds: [...new Set(line.visualNodeIds)],
    }))
    .sort((left, right) => left.displayName.localeCompare(right.displayName) || left.unit.localeCompare(right.unit));
};

const dimensionSummary = (dimensions: Record<string, unknown>) => (
  Object.entries(dimensions)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '')
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key.replace(/_/g, ' ')} ${value}`)
    .join(', ')
);

const lineMetadata = (line: GroupedBomLine) => (
  [
    dimensionSummary(line.dimensions),
    line.material && `material ${line.material}`,
    line.finish && `finish ${line.finish}`,
    line.grade && `grade ${line.grade}`,
    line.standard && `standard ${line.standard}`,
  ].filter(Boolean).join(' | ')
);

const ffd1D = (pieces: number[], stockLength: number) => {
  const sorted = [...pieces].sort((left, right) => right - left);
  const remaining: number[] = [];
  for (const piece of sorted) {
    if (piece > stockLength + 1e-9) return Infinity;
    const index = remaining.findIndex((capacity) => capacity >= piece - 1e-9);
    if (index >= 0) remaining[index] = (remaining[index] || 0) - piece;
    else remaining.push(stockLength - piece);
  }
  return remaining.length;
};

export const priceGroupedLine = (line: GroupedBomLine, pricing: SupplierPricing, supplierId: string): PricedLine => {
  const quantity = line.quantity;
  const packageQuantity = pricing.package_quantity > 0 ? pricing.package_quantity : 1;
  const unitCost = pricing.price_per_package > 0 ? pricing.price_per_package : pricing.price_per_unit;
  const lengthM = asNumber(line.dimensions.length_mm, 0) / 1000;
  const pieces = lengthM > 0
    ? Array.from({ length: Math.max(0, Math.ceil(quantity)) }, () => lengthM)
    : [];

  if (pricing.package_type === 'stock_length') {
    const stockLengths = pricing.stock_lengths.length ? pricing.stock_lengths : [packageQuantity].filter((item) => item > 0);
    let bestStock = 0;
    let bestCount = Infinity;
    let bestCost = Infinity;
    for (const stock of stockLengths) {
      const count = pieces.length ? ffd1D(pieces, stock) : Math.ceil(quantity / stock);
      if (!Number.isFinite(count)) continue;
      const cost = count * stock * pricing.price_per_unit;
      if (cost < bestCost) {
        bestCost = cost;
        bestCount = count;
        bestStock = stock;
      }
    }
    const requiredM = pieces.length ? pieces.reduce((sum, piece) => sum + piece, 0) : quantity;
    const boughtM = Number.isFinite(bestCount) ? bestCount * bestStock : 0;
    return {
      supplier_id: supplierId,
      package_type: pricing.package_type,
      buy_quantity: Number.isFinite(bestCount) ? bestCount : 0,
      purchase_label: Number.isFinite(bestCount) ? `${bestCount} x ${bestStock}m stock` : 'no stock fit',
      waste_quantity: Math.max(0, Number((boughtM - requiredM).toFixed(3))),
      waste_label: `${Math.max(0, boughtM - requiredM).toFixed(3)}m waste`,
      unit_cost: pricing.price_per_unit,
      total_cost: Number.isFinite(bestCost) ? Number(bestCost.toFixed(2)) : 0,
    };
  }

  if (pricing.package_type === 'roll' || pricing.package_type === 'reel') {
    const rollLength = pricing.stock_lengths[0] || packageQuantity;
    const requiredM = lengthM > 0 ? lengthM * quantity : quantity;
    const rolls = rollLength > 0 ? Math.ceil(requiredM / rollLength) : 0;
    const boughtM = rolls * rollLength;
    return {
      supplier_id: supplierId,
      package_type: pricing.package_type,
      buy_quantity: rolls,
      purchase_label: `${rolls} x ${rollLength}m ${pricing.package_type}`,
      waste_quantity: Math.max(0, Number((boughtM - requiredM).toFixed(3))),
      waste_label: `${Math.max(0, boughtM - requiredM).toFixed(3)}m waste`,
      unit_cost: pricing.price_per_unit,
      total_cost: Number((boughtM * pricing.price_per_unit).toFixed(2)),
    };
  }

  if (pricing.package_type === 'pack' || pricing.package_type === 'box') {
    const packages = Math.ceil(quantity / packageQuantity);
    const packageCost = pricing.price_per_package > 0 ? pricing.price_per_package : packageQuantity * pricing.price_per_unit;
    return {
      supplier_id: supplierId,
      package_type: pricing.package_type,
      buy_quantity: packages,
      purchase_label: `${packages} ${pricing.package_type}${packages === 1 ? '' : 'es'} of ${packageQuantity} ${pricing.package_unit || line.unit}`,
      waste_quantity: Math.max(0, Number((packages * packageQuantity - quantity).toFixed(3))),
      waste_label: `${Math.max(0, packages * packageQuantity - quantity).toFixed(3)} spare ${line.unit}`,
      unit_cost: packageCost,
      total_cost: Number((packages * packageCost).toFixed(2)),
    };
  }

  if (pricing.package_type === 'bulk_volume' || pricing.package_type === 'bulk_mass') {
    const packages = pricing.package_quantity > 0 ? Math.ceil(quantity / pricing.package_quantity) : quantity;
    const bought = pricing.package_quantity > 0 ? packages * pricing.package_quantity : quantity;
    const cost = pricing.price_per_package > 0 ? packages * pricing.price_per_package : bought * pricing.price_per_unit;
    return {
      supplier_id: supplierId,
      package_type: pricing.package_type,
      buy_quantity: Number(packages.toFixed(3)),
      purchase_label: pricing.package_quantity > 0
        ? `${packages} x ${pricing.package_quantity} ${pricing.package_unit || line.unit}`
        : `${quantity} ${line.unit}`,
      waste_quantity: Math.max(0, Number((bought - quantity).toFixed(3))),
      waste_label: `${Math.max(0, bought - quantity).toFixed(3)} ${line.unit} surplus`,
      unit_cost: unitCost,
      total_cost: Number(cost.toFixed(2)),
    };
  }

  return {
    supplier_id: supplierId,
    package_type: 'each',
    buy_quantity: quantity,
    purchase_label: `${quantity} ${line.unit}`,
    waste_quantity: 0,
    waste_label: '',
    unit_cost: pricing.price_per_unit,
    total_cost: Number((quantity * pricing.price_per_unit).toFixed(2)),
  };
};

const coverageClass = (state: CoverageState) => {
  if (state === 'valid') return 'bg-emerald-500';
  if (state === 'incomplete') return 'bg-amber-400';
  if (state === 'missing') return 'bg-red-500';
  return 'bg-slate-500';
};

const parseBomNodeName = (name: string) => {
  const match = /^bom:([^:]+):(.+)$/.exec(name);
  if (!match) return null;
  const componentId = match[1];
  const label = match[2];
  return componentId && label ? { componentId, visualNodeId: name, label } : null;
};

type VisualMeshInfo = { componentId: string; visualNodeId: string };

type MaterialSelectionState = {
  transparent: boolean;
  opacity: number;
  depthWrite: boolean;
  emissive?: THREE.Color;
  emissiveIntensity?: number;
};

type EmissiveMaterial = THREE.Material & {
  emissive?: THREE.Color;
  emissiveIntensity?: number;
};

export const BomReviewTab: React.FC<{
  artusServerUrl: string;
  extusServerUrl: string;
  intusServerUrl: string;
  isActive?: boolean;
  onOpenCompiler?: () => void;
}> = ({ artusServerUrl: _artusServerUrl, extusServerUrl, intusServerUrl, isActive = true, onOpenCompiler }) => {
  const { authMode, getAccessToken } = useAuth();
  const storage = useMemo(
    () => createProjectStorage({ authMode, serverUrl: intusServerUrl, getAccessToken }),
    [authMode, getAccessToken, intusServerUrl],
  );
  const [subTab, setSubTab] = useState<SubTab>('bom');
  const [projectName, setProjectName] = useState('');
  const [manifestEnvelope, setManifestEnvelope] = useState<ManifestEnvelope | null>(null);
  const [modelUrl, setModelUrl] = useState('');
  const [statusText, setStatusText] = useState('Waiting for compiled model...');
  const [error, setError] = useState<string | null>(null);
  const [recoveryStatus, setRecoveryStatus] = useState('');
  const [isDraftingBomMetadata, setIsDraftingBomMetadata] = useState(false);
  const [snippetCopied, setSnippetCopied] = useState(false);
  const [procurementAnalysisUnavailable, setProcurementAnalysisUnavailable] = useState(false);
  const [selectedScopeId, setSelectedScopeId] = useState('__all__');
  const [selectedLineKey, setSelectedLineKey] = useState<string | null>(null);
  const [selectedComponentId, setSelectedComponentId] = useState<string | null>(null);
  const [showModelPreview, setShowModelPreview] = useState(false);
  const [visibleRows, setVisibleRows] = useState(INITIAL_VISIBLE_ROWS);
  const [suppliers, setSuppliers] = useState<Supplier[]>(() => readJsonState('procurement_suppliers', [], normalizeSuppliers));
  const [assignments, setAssignments] = useState<Record<string, string>>(() => readJsonState('procurement_assignments', {}));
  const [sectionOverrides, setSectionOverrides] = useState<Record<string, string>>(() => readJsonState('procurement_section_overrides', {}));
  const [activeSupplierId, setActiveSupplierId] = useState<string | null>(() => localStorage.getItem('procurement_active_supplier'));
  const [editSupplier, setEditSupplier] = useState<Supplier | null>(null);
  const [editPricing, setEditPricing] = useState<SupplierPricing | null>(null);

  const artifactManifest = manifestEnvelope?.manifest || null;
  const artifactState = resolveBomArtifactState(manifestEnvelope);
  const manifest = artifactManifest;
  const counts = manifestEnvelope?.manifest_counts || manifestCounts(manifest);
  const isReadyManifest = artifactState === 'ready';
  const canUseAssemblyViews = isReadyManifest || artifactState === 'scopes_only';

  useEffect(() => writeJsonState('procurement_suppliers', suppliers), [suppliers]);
  useEffect(() => writeJsonState('procurement_assignments', assignments), [assignments]);
  useEffect(() => writeJsonState('procurement_section_overrides', sectionOverrides), [sectionOverrides]);
  useEffect(() => {
    if (activeSupplierId) localStorage.setItem('procurement_active_supplier', activeSupplierId);
    else localStorage.removeItem('procurement_active_supplier');
  }, [activeSupplierId]);

  useEffect(() => {
    if (!isActive) return;
    let mounted = true;
    let lastManifestMtime = 0;

    const loadManifest = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const startedAt = performance.now();
        const projectResponse = await apiFetch(`${extusServerUrl}/project_name`, getAccessToken);
        if (projectResponse.ok) {
          const data = await projectResponse.json();
          if (mounted) setProjectName(asString(data.project_name));
        }

        let analysisUnavailable = false;
        let manifestResponse = await apiFetch(`${extusServerUrl}/procurement_analysis`, getAccessToken);
        if (manifestResponse.status === 404) {
          analysisUnavailable = true;
          manifestResponse = await apiFetch(`${extusServerUrl}/bom_manifest`, getAccessToken);
        }
        if (!manifestResponse.ok) {
          if (mounted) {
            setManifestEnvelope(null);
            setProcurementAnalysisUnavailable(analysisUnavailable);
            setError(manifestResponse.status === 404 ? null : 'Failed to read the procurement analysis artifact.');
          }
          return;
        }
        const jsonStartedAt = performance.now();
        let data = normalizeManifestEnvelope((await manifestResponse.json()) as ManifestEnvelope);
        const jsonDurationMs = Math.round(performance.now() - jsonStartedAt);
        if (analysisUnavailable && data?.manifest) {
          const fallbackManifest = {
            ...data.manifest,
            components: [],
            requirements: [],
          };
          data = {
            ...data,
            manifest: fallbackManifest,
            artifact_state: fallbackManifest.scopes.length > 0 ? 'scopes_only' : 'diagnostic_only',
            manifest_counts: manifestCounts(fallbackManifest),
          };
        }
        const durationMs = Math.round(performance.now() - startedAt);
        perfLog('Procurement', 'manifest-fetch-complete', {
          durationMs,
          jsonDurationMs,
          artifactState: data ? resolveBomArtifactState(data) : 'missing_manifest',
          counts: data?.manifest_counts || manifestCounts(data?.manifest),
        }, durationMs >= PROCUREMENT_SYNC_WORK_WARN_MS || jsonDurationMs >= PROCUREMENT_SYNC_WORK_WARN_MS ? 'warn' : 'info');
        if (mounted && data && data.mtime !== lastManifestMtime) {
          lastManifestMtime = data.mtime;
          setManifestEnvelope(data);
          setProcurementAnalysisUnavailable(analysisUnavailable);
          setError(null);
        }
      } catch {
        if (mounted) setError('Failed to connect to the procurement analysis endpoint.');
      }
    };

    loadManifest();
    const interval = setInterval(loadManifest, getPollingDelay(MANIFEST_POLL_INTERVAL_MS));
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [extusServerUrl, getAccessToken, isActive]);

  useEffect(() => {
    if (!isActive) return;
    let mounted = true;
    let lastModelMtime = 0;

    const loadModelStatus = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const response = await apiFetch(`${extusServerUrl}/status`, getAccessToken);
        if (!response.ok) {
          if (mounted) setStatusText('No active model artifact found.');
          return;
        }
        const data = await response.json();
        const mtime = asNumber(data.mtime, 0);
        if (mtime > 0 && mtime !== lastModelMtime && mounted) {
          lastModelMtime = mtime;
          setModelUrl(`${extusServerUrl}/model?t=${mtime}`);
          setStatusText(`Model updated ${new Date(mtime * 1000).toLocaleTimeString()}`);
        }
      } catch {
        if (mounted) setStatusText('Lost connection to the model artifact server.');
      }
    };

    loadModelStatus();
    const interval = setInterval(loadModelStatus, getPollingDelay(MODEL_STATUS_POLL_INTERVAL_MS));
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [extusServerUrl, getAccessToken, isActive]);

  const manifestIndex = useMemo(() => measureProcurementWork(
    'manifest-indexed',
    { counts: manifestCounts(manifest) },
    () => createManifestIndex(manifest),
  ), [manifest]);
  const scopesById = manifestIndex.scopesById;
  const scopeStats = useMemo(() => measureProcurementWork(
    'scope-stats-built',
    { counts: manifestCounts(manifest) },
    () => createScopeStats(manifest, manifestIndex),
  ), [manifest, manifestIndex]);
  const bomLines = useMemo(() => measureProcurementWork(
    'bom-lines-grouped',
    { selectedScopeId, requirements: manifest?.requirements.length || 0 },
    () => groupManifestRequirements(manifest, selectedScopeId, manifestIndex),
  ), [manifest, manifestIndex, selectedScopeId]);
  const allBomLineCount = scopeStats.allItemCount;
  const selectedLine = useMemo(() => bomLines.find((line) => line.key === selectedLineKey) || null, [bomLines, selectedLineKey]);
  const visibleBomRows = useMemo(() => bomLines.slice(0, visibleRows), [bomLines, visibleRows]);

  const scopeOptions = useMemo<ScopeOption[]>(() => {
    if (!manifest || !canUseAssemblyViews) return [];
    const options: ScopeOption[] = [];
    for (const scope of manifest.scopes) {
      options.push({
        id: scope.id,
        label: scope.label,
        depth: scopeDepth(scope, scopesById),
        itemCount: scopeStats.itemCountByScopeId.get(scope.id) || 0,
        componentCount: scopeStats.componentCountByScopeId.get(scope.id) || 0,
      });
    }
    return options;
  }, [canUseAssemblyViews, manifest, scopeStats, scopesById]);

  const componentsById = manifestIndex.componentsById;
  const requirementsByComponent = useMemo(() => {
    const map = new Map<string, ManifestRequirement[]>();
    for (const requirement of manifest?.requirements || []) {
      const list = map.get(requirement.component_id) || [];
      list.push(requirement);
      map.set(requirement.component_id, list);
    }
    return map;
  }, [manifest]);

  const componentCoverage = useMemo<ComponentCoverage[]>(() => {
    return (manifest?.components || []).map((component) => {
      const requirements = requirementsByComponent.get(component.id) || [];
      if (!requirements.length) {
        return { component, requirements, state: 'missing', message: 'No explicit procurement requirement' };
      }
      if (requirements.some((requirement) => !requirementComplete(requirement))) {
        return { component, requirements, state: 'incomplete', message: 'Requirement metadata incomplete' };
      }
      return { component, requirements, state: 'valid', message: 'Requirement metadata valid' };
    });
  }, [manifest, requirementsByComponent]);

  const diagnostics = manifest?.diagnostics || [];
  const selectedComponent = selectedComponentId ? componentsById.get(selectedComponentId) || null : null;
  const selectedComponentRequirements = selectedComponentId ? requirementsByComponent.get(selectedComponentId) || [] : [];

  const uniquePricingSections = useMemo(() => [...new Set(bomLines.map((line) => sectionOverrides[line.key] || line.displayName))], [bomLines, sectionOverrides]);
  const supplierOptionsBySection = useMemo(() => {
    const map = new Map<string, Supplier[]>();
    for (const supplier of suppliers) {
      for (const price of supplier.pricing) {
        const list = map.get(price.section) || [];
        list.push(supplier);
        map.set(price.section, list);
      }
    }
    return map;
  }, [suppliers]);

  const pricedLines = useMemo(() => bomLines.map((line) => {
    const supplierId = assignments[line.key];
    if (!supplierId) return null;
    const supplier = suppliers.find((item) => item.id === supplierId);
    if (!supplier) return null;
    const section = sectionOverrides[line.key] || line.displayName;
    const pricing = supplier.pricing.find((item) => item.section === section || item.section === line.partNumber);
    if (!pricing) return null;
    return priceGroupedLine(line, pricing, supplierId);
  }), [assignments, bomLines, sectionOverrides, suppliers]);

  const totalCost = pricedLines.reduce((sum, line) => sum + (line?.total_cost || 0), 0);
  const pricedCount = pricedLines.filter(Boolean).length;
  const selectedScope = selectedScopeId === '__all__' ? null : scopeOptions.find((scope) => scope.id === selectedScopeId) || null;
  const selectedComponentCount = selectedScope?.componentCount ?? counts.components;
  const showScopeSelector = canUseAssemblyViews && scopeOptions.length > 0;
  const showScopeList = false;
  const showBomToolbar = isReadyManifest || artifactState === 'scopes_only';
  const showBomTable = isReadyManifest && bomLines.length > 0;
  const emptyStateTitle = procurementAnalysisUnavailable && !isReadyManifest
    ? 'Deterministic procurement analysis is not available'
    : isReadyManifest ? 'No procurement requirements in this view' : artifactStateTitle(artifactState);
  const emptyStateMessage = isReadyManifest
    ? `${selectedScope?.label || projectName || 'The current view'} has no declared procurement requirements.`
    : procurementAnalysisUnavailable
      ? 'The BoM tab needs the /api/extus/procurement_analysis endpoint to show the same rows as the procurement analysis viewer. The running backend does not expose that endpoint yet, so GLTF-derived draft rows are hidden.'
    : artifactStateMessage(artifactState);
  const canDraftBomMetadata = authMode !== 'guest' && Boolean(projectName) && !isDraftingBomMetadata;
  const viewerVerificationText = isReadyManifest
    ? 'Verified: model and BoM manifest match'
    : procurementAnalysisUnavailable
      ? 'Procurement analysis endpoint unavailable'
      : `Procurement unverified: ${artifactStateTitle(artifactState)}`;

  useEffect(() => {
    setVisibleRows(INITIAL_VISIBLE_ROWS);
    setSelectedLineKey(null);
  }, [selectedScopeId, manifestEnvelope?.manifest_artifact_id]);

  useEffect(() => {
    setShowModelPreview(false);
  }, [modelUrl]);

  useEffect(() => {
    if (!scopeOptions.length) {
      if (selectedScopeId !== '__all__') setSelectedScopeId('__all__');
      return;
    }
    if (selectedScopeId === '__all__') return;
    if (!scopeOptions.some((scope) => scope.id === selectedScopeId)) setSelectedScopeId(scopeOptions[0]?.id || '__all__');
  }, [scopeOptions, selectedScopeId]);

  const selectedVisualNodeIds = selectedLine?.visualNodeIds || (selectedComponent?.visual_node_ids || []);
  const handleSelectComponent = useCallback((componentId: string) => {
    setSelectedComponentId(componentId);
    setSelectedLineKey(null);
  }, []);

  const copyStarterSnippet = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(BOM_STARTER_SNIPPET);
      setSnippetCopied(true);
      window.setTimeout(() => setSnippetCopied(false), 2_000);
    } catch {
      setRecoveryStatus('Clipboard copy failed. Open Compiler and add tertius_bom metadata manually.');
    }
  }, []);

  const draftBomMetadata = useCallback(async () => {
    if (authMode === 'guest') {
      setRecoveryStatus('Log in before drafting BoM metadata.');
      return;
    }
    if (!projectName) {
      setRecoveryStatus('Select an active project before drafting BoM metadata.');
      return;
    }

    setIsDraftingBomMetadata(true);
    setRecoveryStatus('Preparing design.py for BoM metadata drafting...');
    try {
      const metadata = await storage.listFileMetadata(projectName);
      const editableFiles = metadata
        .filter((file) => file.id && file.updated_at)
        .slice(0, 20)
        .map((file) => ({
          id: file.id,
          filename: file.filename,
          updated_at: file.updated_at as string,
        }));
      const designFile = editableFiles.find((file) => file.filename === 'design.py');
      if (!designFile) {
        setRecoveryStatus('design.py metadata is not available. Open Compiler, refresh the project, then try again.');
        return;
      }

      const job = await storage.applyLlmFileEditJob(projectName, {
        prompt: BOM_METADATA_EDIT_PROMPT,
        files: editableFiles,
        active_file_id: designFile.id,
        metadata: { source: 'procurement_bom_recovery' },
      });
      setRecoveryStatus(`BoM metadata draft queued (${job.job_id}).`);

      for (let attempt = 0; attempt < BOM_RECOVERY_MAX_POLLS; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, BOM_RECOVERY_POLL_MS));
        const status = await storage.getLlmFileEditJob(projectName, job.job_id);
        if (status.status === 'queued' || status.status === 'running') {
          setRecoveryStatus(`BoM metadata draft is ${status.status}...`);
          continue;
        }
        if (status.status === 'succeeded' && status.result) {
          if (status.result.outcome === 'changed') {
            const changed = status.result.files.filter((file) => file.changed).map((file) => file.filename).join(', ');
            setRecoveryStatus(`BoM metadata drafted in ${changed || 'design.py'}. Review it in Compiler, then compile GLB again.`);
          } else {
            setRecoveryStatus(status.result.message || 'The BoM metadata draft finished without changing design.py.');
          }
          return;
        }
        setRecoveryStatus(status.user_message || status.error || 'BoM metadata draft failed.');
        return;
      }
      setRecoveryStatus('BoM metadata draft is still running. Open Compiler to check the edit job history.');
    } catch (err) {
      setRecoveryStatus(err instanceof Error ? err.message : 'BoM metadata draft failed.');
    } finally {
      setIsDraftingBomMetadata(false);
    }
  }, [authMode, projectName, storage]);

  const saveSupplierEdit = () => {
    if (!editSupplier?.name.trim()) return;
    const next = suppliers.some((supplier) => supplier.id === editSupplier.id)
      ? suppliers.map((supplier) => supplier.id === editSupplier.id ? editSupplier : supplier)
      : [...suppliers, editSupplier];
    setSuppliers(next);
    setActiveSupplierId(editSupplier.id);
    setEditSupplier(null);
  };

  const deleteSupplier = (id: string) => {
    setSuppliers((current) => current.filter((supplier) => supplier.id !== id));
    setAssignments((current) => {
      const next = { ...current };
      for (const lineId of Object.keys(next)) {
        if (next[lineId] === id) delete next[lineId];
      }
      return next;
    });
    if (activeSupplierId === id) setActiveSupplierId(null);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900 px-4 py-3">
        <div className="mr-2">
          <div className="text-sm font-bold text-slate-100">Procurement</div>
          <div className="max-w-[360px] truncate text-[11px] text-slate-500">
            {projectName || 'No active project'}
            {manifest?.source_snapshot_hash ? ` | ${manifest.source_snapshot_hash.slice(0, 12)}` : ''}
          </div>
        </div>
        {([
          ['bom', 'Bill of Materials'],
          ['suppliers', 'Suppliers'],
          ['review', 'Review'],
        ] as Array<[SubTab, string]>).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setSubTab(key)}
            className={`rounded px-3 py-2 text-xs font-semibold transition-colors ${subTab === key ? 'bg-amber-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
          >
            {label}
          </button>
        ))}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {isReadyManifest && <div className="text-xs text-slate-400">{pricedCount}/{bomLines.length} lines priced</div>}
          {totalCost > 0 && <div className="rounded border border-emerald-900/60 bg-emerald-950/40 px-3 py-1 text-sm font-bold text-emerald-300">{money(totalCost)} ex GST</div>}
        </div>
      </div>

      {error && <div className="border-b border-amber-900/60 bg-amber-950/30 px-4 py-2 text-sm text-amber-200">{error}</div>}
      {artifactState === 'stale_manifest' && (
        <div className="border-b border-red-900/60 bg-red-950/30 px-4 py-2 text-sm text-red-200">
          The latest BoM manifest and latest 3D model came from different compile jobs. Recompile before treating this BoM as verified.
        </div>
      )}
      {procurementAnalysisUnavailable && !isReadyManifest && (
        <div className="border-b border-amber-900/60 bg-amber-950/30 px-4 py-2 text-sm text-amber-200">
          The running backend does not expose deterministic procurement analysis yet, so GLTF-derived draft rows are hidden.
        </div>
      )}

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {subTab === 'bom' && (
          <>
            {showScopeList && (
              <aside className="w-72 shrink-0 overflow-y-auto border-r border-slate-800 bg-slate-900/50 p-3">
                <div className="mb-3 text-xs font-bold uppercase tracking-wider text-amber-300">BoM view</div>
                <button
                  onClick={() => setSelectedScopeId('__all__')}
                  className={`mb-2 block w-full rounded border p-3 text-left transition-colors ${selectedScopeId === '__all__' ? 'border-amber-500 bg-amber-950/40 text-slate-100' : 'border-slate-800 bg-slate-950 text-slate-300 hover:bg-slate-900'}`}
                >
                  <div className="text-sm font-semibold">Whole design</div>
                  <div className="mt-2 text-xs text-slate-400">
                    {allBomLineCount} requirement groups
                    <span className="mx-1 text-slate-600">·</span>
                    {counts.components} components
                  </div>
                </button>
                {scopeOptions.map((scope) => (
                  <button
                    key={scope.id}
                    onClick={() => setSelectedScopeId(scope.id)}
                    className={`mb-2 block w-full rounded border p-3 text-left transition-colors ${selectedScopeId === scope.id ? 'border-amber-500 bg-amber-950/40 text-slate-100' : 'border-slate-800 bg-slate-950 text-slate-300 hover:bg-slate-900'}`}
                    style={{ paddingLeft: `${12 + Math.min(scope.depth, 6) * 14}px` }}
                  >
                    <div className="truncate text-sm font-semibold" title={scope.label}>{scope.label}</div>
                    <div className="mt-2 text-xs text-slate-400">
                      {scope.itemCount} requirement group{scope.itemCount === 1 ? '' : 's'}
                      <span className="mx-1 text-slate-600">·</span>
                      {scope.componentCount} component{scope.componentCount === 1 ? '' : 's'}
                    </div>
                  </button>
                ))}
              </aside>
            )}
            <main className="min-w-0 flex-1 overflow-auto p-4">
              {showBomToolbar && (
                <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                  {showScopeSelector ? (
                    <label className="flex items-center gap-2">
                      <span className="text-slate-500">BoM view</span>
                      <select
                        value={selectedScopeId}
                        onChange={(event) => {
                          setSelectedScopeId(event.target.value);
                          setSelectedLineKey(null);
                          setSelectedComponentId(null);
                        }}
                        className="w-72 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm font-semibold text-slate-100 outline-none focus:border-amber-500"
                      >
                        <option value="__all__">Whole design ({allBomLineCount})</option>
                        {scopeOptions.map((scope) => (
                          <option key={scope.id} value={scope.id}>
                            {`${'  '.repeat(Math.min(scope.depth, 6))}${scope.label} (${scope.itemCount})`}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <span className="text-slate-500">{selectedScope?.label || 'Whole design'}</span>
                  )}
                  <span>{bomLines.length} grouped requirement{bomLines.length === 1 ? '' : 's'}</span>
                  <span>{selectedComponentCount} registered visual component{selectedComponentCount === 1 ? '' : 's'}</span>
                  <span>{counts.diagnostics} diagnostic{counts.diagnostics === 1 ? '' : 's'}</span>
                </div>
              )}

              {!showBomTable && (
                <div className="rounded border border-slate-800 bg-slate-900/60 p-5">
                  <div className="text-sm font-bold text-slate-100">{emptyStateTitle}</div>
                  <div className="mt-2 max-w-3xl text-sm text-slate-400">{emptyStateMessage}</div>
                  {!isReadyManifest && (
                    <div className="mt-4 rounded border border-amber-900/50 bg-amber-950/20 p-4">
                      <div className="text-xs font-bold uppercase tracking-wider text-amber-300">Recovery</div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          onClick={draftBomMetadata}
                          disabled={!canDraftBomMetadata}
                          className={`rounded px-3 py-2 text-xs font-semibold ${canDraftBomMetadata ? 'bg-amber-600 text-white hover:bg-amber-500' : 'cursor-not-allowed bg-slate-800 text-slate-500'}`}
                        >
                          {isDraftingBomMetadata ? 'Drafting metadata...' : 'Draft BoM metadata'}
                        </button>
                        <button
                          onClick={onOpenCompiler}
                          className="rounded bg-slate-800 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-700"
                        >
                          Open Compiler
                        </button>
                        <button
                          onClick={copyStarterSnippet}
                          className="rounded bg-slate-800 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-700"
                        >
                          {snippetCopied ? 'Snippet copied' : 'Copy starter snippet'}
                        </button>
                      </div>
                      {recoveryStatus && <div className="mt-3 text-xs text-slate-300">{recoveryStatus}</div>}
                      <div className="mt-3 text-xs text-slate-500">
                        The draft action edits design.py through Intus. Review the change, compile GLB again, then Procurement will load the new manifest artifact.
                      </div>
                    </div>
                  )}
                  <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-slate-400 md:grid-cols-4">
                    <div className="rounded border border-slate-800 bg-slate-950 p-3">
                      <div className="text-[11px] uppercase tracking-wider text-slate-600">Scopes</div>
                      <div className="mt-1 text-lg font-bold text-slate-200">{counts.scopes}</div>
                    </div>
                    <div className="rounded border border-slate-800 bg-slate-950 p-3">
                      <div className="text-[11px] uppercase tracking-wider text-slate-600">Components</div>
                      <div className="mt-1 text-lg font-bold text-slate-200">{counts.components}</div>
                    </div>
                    <div className="rounded border border-slate-800 bg-slate-950 p-3">
                      <div className="text-[11px] uppercase tracking-wider text-slate-600">Requirements</div>
                      <div className="mt-1 text-lg font-bold text-slate-200">{counts.requirements}</div>
                    </div>
                    <div className="rounded border border-slate-800 bg-slate-950 p-3">
                      <div className="text-[11px] uppercase tracking-wider text-slate-600">Diagnostics</div>
                      <div className="mt-1 text-lg font-bold text-slate-200">{counts.diagnostics}</div>
                    </div>
                  </div>
                  {diagnostics.length > 0 && (
                    <div className="mt-4 overflow-hidden rounded border border-slate-800 bg-slate-950">
                      <div className="border-b border-slate-800 bg-slate-900 px-3 py-2 text-xs font-bold uppercase tracking-wider text-amber-300">Diagnostics</div>
                      <div className="divide-y divide-slate-900">
                        {diagnostics.slice(0, 6).map((diagnostic, index) => (
                          <div key={`${diagnostic.code}-${index}`} className="p-3 text-xs">
                            <div className={diagnostic.severity === 'error' ? 'font-semibold text-red-300' : diagnostic.severity === 'warning' ? 'font-semibold text-amber-300' : 'font-semibold text-slate-300'}>
                              {diagnostic.code}
                            </div>
                            <div className="mt-1 text-slate-300">{diagnostic.message}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {showBomTable && (
                <>
                  <datalist id="procurement-section-options">
                    {uniquePricingSections.map((item) => <option key={item} value={item} />)}
                  </datalist>

                  <div className="overflow-hidden rounded border border-slate-800">
                <table className="w-full min-w-[1120px] border-collapse text-left text-xs">
                  <thead className="sticky top-0 bg-slate-900 text-slate-400">
                    <tr>
                      <th className="px-3 py-2">State</th>
                      <th className="px-3 py-2">Requirement</th>
                      <th className="px-3 py-2 text-right">Qty</th>
                      <th className="px-3 py-2">Identity</th>
                      <th className="px-3 py-2">Supplier pricing</th>
                      <th className="px-3 py-2">Supplier</th>
                      <th className="px-3 py-2 text-right">Buy</th>
                      <th className="px-3 py-2 text-right">Waste</th>
                      <th className="px-3 py-2 text-right">Unit $</th>
                      <th className="px-3 py-2 text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleBomRows.map((line, index) => {
                      const pricingSection = sectionOverrides[line.key] || line.displayName;
                      const supplierId = assignments[line.key] || '';
                      const suppliersForSection = supplierOptionsBySection.get(pricingSection) || [];
                      const priced = pricedLines[index];
                      const metadata = lineMetadata(line);
                      return (
                        <tr
                          key={line.key}
                          onClick={() => { setSelectedLineKey(line.key); setSelectedComponentId(null); }}
                          className={`cursor-pointer border-t border-slate-900 align-top ${selectedLineKey === line.key ? 'bg-amber-950/30' : 'bg-slate-950 hover:bg-slate-900/80'}`}
                        >
                          <td className="px-3 py-3">
                            <span className={`inline-block h-3 w-3 rounded-full ${coverageClass(line.status)}`} title={line.status} />
                          </td>
                          <td className="max-w-[260px] px-3 py-3">
                            <div className="font-semibold text-slate-100">{line.displayName}</div>
                            <div className="mt-1 text-[11px] text-slate-500">{line.componentIds.length} visual component{line.componentIds.length === 1 ? '' : 's'}</div>
                            {metadata && <div className="mt-1 truncate text-[11px] text-slate-500" title={metadata}>{metadata}</div>}
                          </td>
                          <td className="px-3 py-3 text-right font-semibold text-slate-200">{line.quantity} {line.unit}</td>
                          <td className="px-3 py-3 text-slate-300">
                            <div>{line.partNumber || '(missing)'}</div>
                            <div className="mt-1 text-[11px] text-slate-600">{line.key}</div>
                          </td>
                          <td className="px-3 py-3">
                            <input
                              value={pricingSection}
                              list="procurement-section-options"
                              onChange={(event) => setSectionOverrides((current) => ({ ...current, [line.key]: event.target.value }))}
                              onClick={(event) => event.stopPropagation()}
                              className="w-44 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
                            />
                          </td>
                          <td className="px-3 py-3">
                            <select
                              value={supplierId}
                              onChange={(event) => setAssignments((current) => ({ ...current, [line.key]: event.target.value }))}
                              onClick={(event) => event.stopPropagation()}
                              className="max-w-40 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
                            >
                              <option value="">none</option>
                              {suppliersForSection.map((supplier) => (
                                <option key={supplier.id} value={supplier.id}>{supplier.name}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-3 py-3 text-right text-slate-300">{priced?.purchase_label || '-'}</td>
                          <td className="px-3 py-3 text-right text-slate-300">{priced?.waste_label || '-'}</td>
                          <td className="px-3 py-3 text-right text-slate-300">{priced ? money(priced.unit_cost) : '-'}</td>
                          <td className="px-3 py-3 text-right font-bold text-emerald-300">{priced ? money(priced.total_cost) : '-'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                  {totalCost > 0 && (
                    <tfoot className="bg-slate-900">
                      <tr>
                        <td colSpan={9} className="px-3 py-2 text-right font-semibold text-slate-400">Total ex GST</td>
                        <td className="px-3 py-2 text-right font-bold text-emerald-300">{money(totalCost)}</td>
                      </tr>
                      <tr>
                        <td colSpan={9} className="px-3 py-2 text-right text-[11px] text-slate-500">Inc GST</td>
                        <td className="px-3 py-2 text-right text-[11px] text-slate-400">{money(totalCost * 1.1)}</td>
                      </tr>
                    </tfoot>
                  )}
                </table>
                  </div>
                  {visibleRows < bomLines.length && (
                    <div className="mt-3 flex justify-center">
                      <button
                        onClick={() => setVisibleRows((current) => Math.min(current + INITIAL_VISIBLE_ROWS, bomLines.length))}
                        className="rounded bg-slate-800 px-4 py-2 text-xs text-slate-300 hover:bg-slate-700"
                      >
                        Show next {Math.min(INITIAL_VISIBLE_ROWS, bomLines.length - visibleRows)} rows
                      </button>
                    </div>
                  )}
                </>
              )}
            </main>

            <aside className="flex w-[38%] min-w-[380px] max-w-[620px] flex-col border-l border-slate-800 bg-slate-900/50">
              {showModelPreview ? (
                <div className="relative min-h-0 flex-1">
                  <ProcurementSceneViewer
                    modelUrl={modelUrl}
                    getAccessToken={getAccessToken}
                    selectedVisualNodeIds={selectedVisualNodeIds}
                    visualComponents={manifest?.components || []}
                    onSelectComponent={handleSelectComponent}
                    onModelLoaded={() => undefined}
                    statusText={statusText}
                    verificationText={viewerVerificationText}
                    isVerified={isReadyManifest}
                    isActive={isActive}
                  />
                  <button
                    type="button"
                    onClick={() => setShowModelPreview(false)}
                    className="absolute right-3 bottom-3 rounded bg-slate-950/90 px-3 py-2 text-xs font-semibold text-slate-200 ring-1 ring-slate-700 hover:bg-slate-900"
                  >
                    Hide 3D preview
                  </button>
                </div>
              ) : (
                <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-slate-950 p-6 text-center">
                  <div className={`rounded px-3 py-2 text-xs ${isReadyManifest ? 'bg-emerald-950/80 text-emerald-200' : 'bg-red-950/80 text-red-200'}`}>
                    {viewerVerificationText}
                  </div>
                  <div className="text-sm font-semibold text-slate-200">3D preview paused</div>
                  <div className="max-w-sm text-xs leading-5 text-slate-500">
                    {statusText}
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowModelPreview(true)}
                    disabled={!modelUrl}
                    className="rounded bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
                  >
                    Load 3D preview
                  </button>
                </div>
              )}
              <SelectionPanel
                selectedLine={selectedLine}
                selectedComponent={selectedComponent}
                selectedComponentRequirements={selectedComponentRequirements}
                componentsById={componentsById}
              />
            </aside>
          </>
        )}

        {subTab === 'suppliers' && (
          <>
            <aside className="w-64 shrink-0 overflow-y-auto border-r border-slate-800 bg-slate-900/50 p-3">
              <div className="mb-3 text-xs font-bold uppercase tracking-wider text-amber-300">Suppliers</div>
              {suppliers.map((supplier) => (
                <button
                  key={supplier.id}
                  onClick={() => { setActiveSupplierId(supplier.id); setEditSupplier(null); setEditPricing(null); }}
                  className={`mb-2 block w-full rounded border p-3 text-left ${activeSupplierId === supplier.id ? 'border-amber-500 bg-amber-950/30' : 'border-slate-800 bg-slate-950 hover:bg-slate-900'}`}
                >
                  <div className="text-sm font-semibold text-slate-100">{supplier.name || '(unnamed)'}</div>
                  {supplier.contact && <div className="mt-1 text-xs text-slate-500">{supplier.contact}</div>}
                  <div className="mt-2 text-xs text-slate-400">{supplier.pricing.length} price rule{supplier.pricing.length === 1 ? '' : 's'}</div>
                </button>
              ))}
              <button onClick={() => { const supplier = newSupplier(); setEditSupplier(supplier); setActiveSupplierId(supplier.id); }} className="mt-2 w-full rounded bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700">Add supplier</button>
            </aside>

            <main className="min-w-0 flex-1 overflow-y-auto p-4">
              {editSupplier ? (
                <SupplierEditor supplier={editSupplier} setSupplier={setEditSupplier} onSave={saveSupplierEdit} onCancel={() => setEditSupplier(null)} />
              ) : activeSupplierId && suppliers.some((supplier) => supplier.id === activeSupplierId) ? (
                <SupplierDetail
                  supplier={suppliers.find((supplier) => supplier.id === activeSupplierId) as Supplier}
                  suppliers={suppliers}
                  setSuppliers={setSuppliers}
                  onEdit={() => {
                    const supplier = suppliers.find((item) => item.id === activeSupplierId);
                    if (supplier) setEditSupplier({ ...supplier, pricing: supplier.pricing.map((price) => ({ ...price, stock_lengths: [...price.stock_lengths] })) });
                  }}
                  onDelete={() => deleteSupplier(activeSupplierId)}
                  editPricing={editPricing}
                  setEditPricing={setEditPricing}
                  sections={uniquePricingSections}
                />
              ) : (
                <div className="py-16 text-center text-sm text-slate-500">Select a supplier or add a new one.</div>
              )}
            </main>
          </>
        )}

        {subTab === 'review' && (
          <main className="min-w-0 flex-1 overflow-auto p-4">
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="overflow-hidden rounded border border-slate-800">
                <table className="w-full min-w-[840px] text-left text-xs">
                  <thead className="bg-slate-900 text-slate-400">
                    <tr>
                      <th className="px-3 py-2">State</th>
                      <th className="px-3 py-2">Visual component</th>
                      <th className="px-3 py-2">Scope</th>
                      <th className="px-3 py-2">Requirements</th>
                      <th className="px-3 py-2">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {componentCoverage.map(({ component, requirements, state, message }) => {
                      const scope = component.scope_id ? scopesById.get(component.scope_id) : null;
                      return (
                        <tr
                          key={component.id}
                          onClick={() => { setSelectedComponentId(component.id); setSelectedLineKey(null); }}
                          className={`cursor-pointer border-t border-slate-900 align-top ${selectedComponentId === component.id ? 'bg-amber-950/30' : 'bg-slate-950 hover:bg-slate-900'}`}
                        >
                          <td className="px-3 py-3"><span className={`inline-block h-3 w-3 rounded-full ${coverageClass(state)}`} title={state} /></td>
                          <td className="max-w-[320px] px-3 py-3">
                            <div className="font-semibold text-slate-100">{component.label}</div>
                            <div className="mt-1 text-[11px] text-slate-500">{component.id}</div>
                            <div className="mt-1 text-[11px] text-slate-500">{message}</div>
                          </td>
                          <td className="px-3 py-3 text-slate-300">{scope?.label || 'Whole design'}</td>
                          <td className="px-3 py-3 text-slate-300">
                            {requirements.length ? requirements.map((requirement) => (
                              <div key={requirement.id}>{displayAlias(asString(requirement.part_number), requirement.dimensions || {})} x {requirementQuantity(requirement)} {requirement.unit || 'each'}</div>
                            )) : '-'}
                          </td>
                          <td className="px-3 py-3 text-slate-400">
                            {component.source_file ? `${component.source_file}${component.source_line ? `:${component.source_line}` : ''}` : '-'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="overflow-hidden rounded border border-slate-800 bg-slate-950">
                <div className="border-b border-slate-800 bg-slate-900 px-3 py-2 text-xs font-bold uppercase tracking-wider text-amber-300">Diagnostics</div>
                <div className="max-h-[520px] overflow-auto p-3">
                  {diagnostics.length ? diagnostics.map((diagnostic, index) => (
                    <div key={`${diagnostic.code}-${index}`} className="mb-3 border-b border-slate-900 pb-3 text-xs">
                      <div className={diagnostic.severity === 'error' ? 'font-semibold text-red-300' : diagnostic.severity === 'warning' ? 'font-semibold text-amber-300' : 'font-semibold text-slate-300'}>
                        {diagnostic.code}
                      </div>
                      <div className="mt-1 text-slate-300">{diagnostic.message}</div>
                      {(diagnostic.source_file || diagnostic.component_id) && (
                        <div className="mt-1 text-[11px] text-slate-600">
                          {[diagnostic.source_file && `${diagnostic.source_file}${diagnostic.source_line ? `:${diagnostic.source_line}` : ''}`, diagnostic.component_id].filter(Boolean).join(' | ')}
                        </div>
                      )}
                    </div>
                  )) : (
                    <div className="py-10 text-center text-sm text-slate-500">No BoM lint diagnostics.</div>
                  )}
                </div>
              </div>
            </div>
          </main>
        )}
      </div>
    </div>
  );
};

const SelectionPanel: React.FC<{
  selectedLine: GroupedBomLine | null;
  selectedComponent: ManifestComponent | null;
  selectedComponentRequirements: ManifestRequirement[];
  componentsById: Map<string, ManifestComponent>;
}> = ({ selectedLine, selectedComponent, selectedComponentRequirements, componentsById }) => (
  <div className="max-h-[34%] overflow-auto border-t border-slate-800 bg-slate-950 p-3 text-xs">
    {selectedLine ? (
      <div>
        <div className="mb-2 text-sm font-bold text-slate-100">{selectedLine.displayName}</div>
        <div className="mb-2 text-slate-400">{selectedLine.quantity} {selectedLine.unit} required from {selectedLine.componentIds.length} component{selectedLine.componentIds.length === 1 ? '' : 's'}.</div>
        {selectedLine.componentIds.slice(0, 12).map((componentId) => {
          const component = componentsById.get(componentId);
          return (
            <div key={componentId} className="mb-1 truncate text-slate-500" title={componentId}>
              {component?.label || componentId}
              {component?.source_file ? ` | ${component.source_file}${component.source_line ? `:${component.source_line}` : ''}` : ''}
            </div>
          );
        })}
      </div>
    ) : selectedComponent ? (
      <div>
        <div className="mb-2 text-sm font-bold text-slate-100">{selectedComponent.label}</div>
        <div className="mb-2 text-slate-500">{selectedComponent.id}</div>
        <div className="mb-2 text-slate-400">
          {selectedComponent.source_file ? `${selectedComponent.source_file}${selectedComponent.source_line ? `:${selectedComponent.source_line}` : ''}` : 'No source trace'}
        </div>
        {selectedComponentRequirements.length ? selectedComponentRequirements.map((requirement) => (
          <div key={requirement.id} className="mb-2 rounded border border-slate-800 bg-slate-900 p-2">
            <div className="font-semibold text-slate-100">{displayAlias(asString(requirement.part_number), requirement.dimensions || {})}</div>
            <div className="text-slate-400">{requirementQuantity(requirement)} {requirement.unit || 'each'}</div>
            {dimensionSummary(requirement.dimensions || {}) && <div className="text-slate-500">{dimensionSummary(requirement.dimensions || {})}</div>}
          </div>
        )) : (
          <div className="text-red-300">No procurement requirements attached.</div>
        )}
      </div>
    ) : (
      <div className="py-8 text-center text-slate-500">Select a BoM row or visual component to inspect the source trace.</div>
    )}
  </div>
);

const ProcurementSceneViewer: React.FC<{
  modelUrl: string;
  getAccessToken: () => Promise<string>;
  selectedVisualNodeIds: string[];
  visualComponents: ManifestComponent[];
  onSelectComponent: (componentId: string) => void;
  onModelLoaded: (root: THREE.Object3D | null) => void;
  statusText: string;
  verificationText: string;
  isVerified: boolean;
  isActive: boolean;
}> = ({ modelUrl, getAccessToken, selectedVisualNodeIds, visualComponents, onSelectComponent, onModelLoaded, statusText, verificationText, isVerified, isActive }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const dimMaterialRef = useRef<THREE.MeshBasicMaterial | null>(null);
  const modelRef = useRef<THREE.Object3D | null>(null);
  const visualComponentsRef = useRef(visualComponents);
  const selectedVisualNodeIdsRef = useRef(selectedVisualNodeIds);
  const frameRef = useRef<number>(0);
  const raycasterRef = useRef(new THREE.Raycaster());
  const pointerRef = useRef(new THREE.Vector2());
  const meshInfoRef = useRef<Map<string, VisualMeshInfo>>(new Map());
  const allMeshesRef = useRef<THREE.Mesh[]>([]);
  const visualMeshesRef = useRef<Map<string, THREE.Mesh[]>>(new Map());
  const meshBoundsRef = useRef<WeakMap<THREE.Mesh, THREE.Box3>>(new WeakMap());
  const materialStateRef = useRef<WeakMap<THREE.Material, MaterialSelectionState>>(new WeakMap());
  const selectedMeshesRef = useRef<Set<THREE.Mesh>>(new Set());
  const selectionDimActiveRef = useRef(false);
  const selectionUpdateRunRef = useRef(0);
  const isActiveRef = useRef(isActive);
  const renderRequestedRef = useRef(true);
  const wakeRenderLoopRef = useRef<(() => void) | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const requestRender = useCallback(() => {
    renderRequestedRef.current = true;
    wakeRenderLoopRef.current?.();
  }, []);

  const getMaterialState = useCallback((material: THREE.Material): MaterialSelectionState => {
    const existing = materialStateRef.current.get(material);
    if (existing) return existing;
    const emissiveMaterial = material as EmissiveMaterial;
    const state: MaterialSelectionState = {
      transparent: material.transparent,
      opacity: material.opacity,
      depthWrite: material.depthWrite,
      emissive: emissiveMaterial.emissive?.clone(),
      emissiveIntensity: typeof emissiveMaterial.emissiveIntensity === 'number'
        ? emissiveMaterial.emissiveIntensity
        : undefined,
    };
    materialStateRef.current.set(material, state);
    return state;
  }, []);

  const applySelectionMaterial = useCallback((mesh: THREE.Mesh, highlighted: boolean, hasSelection: boolean) => {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const material of materials) {
      const original = getMaterialState(material);
      const emissiveMaterial = material as EmissiveMaterial;
      if (!hasSelection) {
        material.transparent = original.transparent;
        material.opacity = original.opacity;
        material.depthWrite = original.depthWrite;
        if (emissiveMaterial.emissive && original.emissive) emissiveMaterial.emissive.copy(original.emissive);
        if (typeof original.emissiveIntensity === 'number') emissiveMaterial.emissiveIntensity = original.emissiveIntensity;
      } else if (highlighted) {
        material.transparent = original.transparent;
        material.opacity = original.opacity;
        material.depthWrite = original.depthWrite;
        if (emissiveMaterial.emissive) emissiveMaterial.emissive.setHex(SELECTION_EMISSIVE);
        if (typeof emissiveMaterial.emissiveIntensity === 'number') emissiveMaterial.emissiveIntensity = 0.35;
      } else {
        material.transparent = true;
        material.opacity = Math.min(original.opacity, DIMMED_SELECTION_OPACITY);
        material.depthWrite = false;
        if (emissiveMaterial.emissive && original.emissive) emissiveMaterial.emissive.copy(original.emissive);
        if (typeof original.emissiveIntensity === 'number') emissiveMaterial.emissiveIntensity = original.emissiveIntensity;
      }
      material.needsUpdate = true;
    }
  }, [getMaterialState]);

  const renderScene = useCallback(() => {
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    const dimMaterial = dimMaterialRef.current;
    if (!renderer || !scene || !camera) return;

    renderer.clear();
    if (selectionDimActiveRef.current && dimMaterial) {
      scene.overrideMaterial = dimMaterial;
      camera.layers.set(0);
      renderer.render(scene, camera);
      renderer.clearDepth();
      scene.overrideMaterial = null;
      camera.layers.set(SELECTION_LAYER);
      renderer.render(scene, camera);
      camera.layers.set(0);
      return;
    }

    scene.overrideMaterial = null;
    camera.layers.set(0);
    renderer.render(scene, camera);
  }, []);

  const focusCameraOnBox = useCallback((box: THREE.Box3) => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || box.isEmpty()) return;

    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const center = sphere.center;
    const radius = Math.max(sphere.radius, 1);
    const fov = THREE.MathUtils.degToRad(camera.fov);
    const distance = Math.max(radius / Math.sin(fov / 2), radius * 3);
    const currentDirection = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
    if (currentDirection.lengthSq() === 0) currentDirection.set(1, -1, 0.7).normalize();

    camera.position.copy(center).addScaledVector(currentDirection, distance * 1.25);
    camera.near = Math.max(0.1, distance / 10_000);
    camera.far = Math.max(distance * 20, radius * 30);
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
    requestRender();
  }, [requestRender]);

  const rebuildMeshIndex = useCallback((root: THREE.Object3D) => {
    const visualIdToComponent = new Map<string, string>();
    for (const component of visualComponentsRef.current) {
      for (const visualId of component.visual_node_ids || []) {
        visualIdToComponent.set(visualId, component.id);
      }
    }

    const allMeshes: THREE.Mesh[] = [];
    const meshInfo = new Map<string, VisualMeshInfo>();
    const visualMeshes = new Map<string, THREE.Mesh[]>();
    const meshBounds = new WeakMap<THREE.Mesh, THREE.Box3>();
    materialStateRef.current = new WeakMap();

    const addVisualMesh = (visualNodeId: string, mesh: THREE.Mesh) => {
      const list = visualMeshes.get(visualNodeId) || [];
      list.push(mesh);
      visualMeshes.set(visualNodeId, list);
    };

    const visit = (object: THREE.Object3D, inheritedInfo: VisualMeshInfo | null) => {
      const uuidComponentId = visualIdToComponent.get(object.uuid);
      const nameComponentId = visualIdToComponent.get(object.name);
      const explicitVisualNodeId = uuidComponentId ? object.uuid : nameComponentId ? object.name : null;
      const parsed = parseBomNodeName(object.name);
      const ownInfo = explicitVisualNodeId
        ? { componentId: uuidComponentId || nameComponentId || '', visualNodeId: explicitVisualNodeId }
        : parsed
          ? { componentId: parsed.componentId, visualNodeId: parsed.visualNodeId }
          : null;
      const info = ownInfo || inheritedInfo;

      if ((object as THREE.Mesh).isMesh) {
        const mesh = object as THREE.Mesh;
        mesh.layers.enable(0);
        mesh.layers.disable(SELECTION_LAYER);
        allMeshes.push(mesh);
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        materials.forEach(getMaterialState);
        if (info?.componentId) {
          meshInfo.set(mesh.uuid, info);
          addVisualMesh(info.visualNodeId, mesh);
        }
      }

      for (const child of object.children) {
        visit(child, info);
      }
    };

    visit(root, null);
    allMeshesRef.current = allMeshes;
    meshInfoRef.current = meshInfo;
    visualMeshesRef.current = visualMeshes;
    meshBoundsRef.current = meshBounds;
    selectedMeshesRef.current = new Set();
    selectionDimActiveRef.current = false;
  }, [getMaterialState]);

  const scheduleSelectionUpdate = useCallback((visualNodeIds: string[]) => {
    const runId = selectionUpdateRunRef.current + 1;
    selectionUpdateRunRef.current = runId;

    if (!ENABLE_PROCUREMENT_3D_SELECTION) {
      selectedMeshesRef.current.forEach((mesh) => {
        mesh.layers.disable(SELECTION_LAYER);
        applySelectionMaterial(mesh, false, false);
      });
      selectedMeshesRef.current = new Set();
      selectionDimActiveRef.current = false;
      requestRender();
      return;
    }

    const selectedIds = new Set(visualNodeIds);
    const nextSelectedMeshes = new Set<THREE.Mesh>();
    const selectionBox = new THREE.Box3();
    for (const visualNodeId of selectedIds) {
      const meshes = visualMeshesRef.current.get(visualNodeId) || [];
      for (const mesh of meshes) {
        if (nextSelectedMeshes.has(mesh)) continue;
        nextSelectedMeshes.add(mesh);
        let bounds = meshBoundsRef.current.get(mesh);
        if (!bounds) {
          bounds = new THREE.Box3().setFromObject(mesh);
          meshBoundsRef.current.set(mesh, bounds);
        }
        selectionBox.union(bounds);
      }
    }

    const hasSelection = nextSelectedMeshes.size > 0;
    if (hasSelection && !selectionBox.isEmpty()) focusCameraOnBox(selectionBox);

    const previousSelectedMeshes = selectedMeshesRef.current;
    const targetMeshes = new Set<THREE.Mesh>();
    previousSelectedMeshes.forEach((mesh) => targetMeshes.add(mesh));
    nextSelectedMeshes.forEach((mesh) => targetMeshes.add(mesh));

    selectedMeshesRef.current = nextSelectedMeshes;
    selectionDimActiveRef.current = hasSelection;

    const targets = Array.from(targetMeshes);
    let cursor = 0;
    const processChunk = () => {
      if (selectionUpdateRunRef.current !== runId) return;
      const end = Math.min(cursor + SELECTION_UPDATE_CHUNK_SIZE, targets.length);
      for (; cursor < end; cursor += 1) {
        const mesh = targets[cursor];
        if (!mesh) continue;
        const highlighted = nextSelectedMeshes.has(mesh);
        if (highlighted) {
          mesh.layers.enable(SELECTION_LAYER);
          applySelectionMaterial(mesh, true, true);
        } else {
          mesh.layers.disable(SELECTION_LAYER);
          applySelectionMaterial(mesh, false, false);
        }
      }
      requestRender();
      if (cursor < targets.length) {
        requestAnimationFrame(processChunk);
      }
    };

    if (targets.length === 0) {
      requestRender();
      return;
    }
    window.setTimeout(processChunk, 0);
  }, [applySelectionMaterial, focusCameraOnBox, requestRender]);

  useEffect(() => {
    isActiveRef.current = isActive;
    if (controlsRef.current) controlsRef.current.enabled = isActive;
    requestRender();
  }, [isActive, requestRender]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x020617);
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100_000);
    const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'low-power' });
    renderer.setPixelRatio(PREVIEW_PIXEL_RATIO);
    renderer.autoClear = false;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);
    const dimMaterial = new THREE.MeshBasicMaterial({
      color: 0x94a3b8,
      transparent: true,
      opacity: DIMMED_SELECTION_OPACITY,
      depthWrite: false,
    });

    const ambient = new THREE.AmbientLight(0xffffff, 0.7);
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(80, -120, 160);
    scene.add(ambient, key);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enabled = isActiveRef.current;

    sceneRef.current = scene;
    cameraRef.current = camera;
    rendererRef.current = renderer;
    controlsRef.current = controls;
    dimMaterialRef.current = dimMaterial;

    const resize = () => {
      const width = Math.max(1, container.clientWidth);
      const height = Math.max(1, container.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
      requestRender();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    const wakeFromInteraction = () => requestRender();
    controls.addEventListener('start', wakeFromInteraction);
    renderer.domElement.addEventListener('pointerdown', wakeFromInteraction);
    renderer.domElement.addEventListener('wheel', wakeFromInteraction);

    let idleTimeoutId: number | undefined;
    const clearIdleTimeout = () => {
      if (!idleTimeoutId) return;
      window.clearTimeout(idleTimeoutId);
      idleTimeoutId = undefined;
    };
    function scheduleIdleCheck(delayMs = SCENE_IDLE_CHECK_MS) {
      clearIdleTimeout();
      idleTimeoutId = window.setTimeout(animate, delayMs);
    }
    function wakeRenderLoop() {
      if (!isActiveRef.current || frameRef.current) return;
      clearIdleTimeout();
      frameRef.current = requestAnimationFrame(animate);
    }
    function animate() {
      frameRef.current = 0;
      clearIdleTimeout();

      if (!isActiveRef.current) {
        scheduleIdleCheck(500);
        return;
      }

      const controlsChanged = Boolean(controls.update());
      const shouldRender = renderRequestedRef.current || controlsChanged;
      if (shouldRender) {
        renderRequestedRef.current = false;
        renderScene();
      }

      if (controlsChanged || renderRequestedRef.current) {
        frameRef.current = requestAnimationFrame(animate);
        return;
      }

      scheduleIdleCheck();
    }
    wakeRenderLoopRef.current = wakeRenderLoop;
    animate();

    const handlePointerDown = (event: PointerEvent) => {
      if (!ENABLE_PROCUREMENT_3D_SELECTION) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointerRef.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointerRef.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycasterRef.current.setFromCamera(pointerRef.current, camera);
      const hit = raycasterRef.current.intersectObjects(allMeshesRef.current, false)[0];
      if (!hit) return;
      let current: THREE.Object3D | null = hit.object;
      while (current) {
        const info = current instanceof THREE.Mesh ? meshInfoRef.current.get(current.uuid) : null;
        const parsed = info || parseBomNodeName(current.name);
        if (parsed?.componentId) {
          onSelectComponent(parsed.componentId);
          return;
        }
        current = current.parent;
      }
    };
    renderer.domElement.addEventListener('pointerdown', handlePointerDown);

    return () => {
      selectionUpdateRunRef.current += 1;
      wakeRenderLoopRef.current = null;
      renderer.domElement.removeEventListener('pointerdown', handlePointerDown);
      renderer.domElement.removeEventListener('pointerdown', wakeFromInteraction);
      renderer.domElement.removeEventListener('wheel', wakeFromInteraction);
      observer.disconnect();
      controls.removeEventListener('start', wakeFromInteraction);
      cancelAnimationFrame(frameRef.current);
      clearIdleTimeout();
      controls.dispose();
      allMeshesRef.current = [];
      visualMeshesRef.current = new Map();
      meshInfoRef.current = new Map();
      meshBoundsRef.current = new WeakMap();
      materialStateRef.current = new WeakMap();
      selectedMeshesRef.current = new Set();
      selectionDimActiveRef.current = false;
      modelRef.current?.traverse((object) => {
        if ((object as THREE.Mesh).isMesh) {
          const mesh = object as THREE.Mesh;
          mesh.geometry.dispose();
          if (Array.isArray(mesh.material)) mesh.material.forEach((material) => material.dispose());
          else mesh.material.dispose();
        }
      });
      renderer.dispose();
      dimMaterial.dispose();
      dimMaterialRef.current = null;
      renderer.domElement.remove();
      onModelLoaded(null);
    };
  }, [onModelLoaded, onSelectComponent, renderScene, requestRender]);

  useEffect(() => {
    if (!modelUrl || !isActive || !sceneRef.current) return;
    let cancelled = false;
    const loader = new GLTFLoader();
    const startedAt = performance.now();
    perfLog('Procurement.Viewer', 'model-fetch-start', { url: modelUrl }, 'info');

    apiFetch(modelUrl, getAccessToken)
      .then((response) => {
        if (!response.ok) throw new Error('model fetch failed');
        return response.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) return;
        perfLog('Procurement.Viewer', 'model-fetch-complete', {
          durationMs: Math.round(performance.now() - startedAt),
          bytes: buffer.byteLength,
        }, 'info');
        const parseStartedAt = performance.now();
        loader.parse(buffer, '', (gltf) => {
          if (cancelled || !sceneRef.current || !cameraRef.current || !controlsRef.current) return;
      perfLog('Procurement.Viewer', 'model-parse-complete', {
        durationMs: Math.round(performance.now() - parseStartedAt),
      }, 'info');
      const setupStartedAt = performance.now();
      if (modelRef.current) sceneRef.current.remove(modelRef.current);
      selectionUpdateRunRef.current += 1;

      const root = gltf.scene;
      selectedMeshesRef.current.forEach((mesh) => mesh.layers.disable(SELECTION_LAYER));
      selectedMeshesRef.current = new Set();
      selectionDimActiveRef.current = false;
      root.traverse((object) => {
            if ((object as THREE.Mesh).isMesh) {
              const mesh = object as THREE.Mesh;
              const materials = Array.isArray(mesh.material) ? mesh.material.map((material) => material.clone()) : mesh.material.clone();
              mesh.material = materials;
            }
          });

          sceneRef.current.add(root);
          modelRef.current = root;
          rebuildMeshIndex(root);
          onModelLoaded(root);
          const box = new THREE.Box3().setFromObject(root);
          const center = box.getCenter(new THREE.Vector3());
          const size = box.getSize(new THREE.Vector3());
          const maxDim = Math.max(size.x, size.y, size.z, 1);
          cameraRef.current.position.set(center.x + maxDim * 1.4, center.y - maxDim * 1.8, center.z + maxDim * 1.2);
          cameraRef.current.near = Math.max(0.1, maxDim / 10_000);
          cameraRef.current.far = maxDim * 20;
          cameraRef.current.updateProjectionMatrix();
          controlsRef.current.target.copy(center);
          controlsRef.current.update();
          setLoadError(null);
          scheduleSelectionUpdate(selectedVisualNodeIdsRef.current);
          requestRender();
          perfLog('Procurement.Viewer', 'model-setup-complete', {
            durationMs: Math.round(performance.now() - setupStartedAt),
            totalDurationMs: Math.round(performance.now() - startedAt),
            meshCount: allMeshesRef.current.length,
          }, 'info');
        }, () => setLoadError('Failed to parse the current model artifact.'));
      })
      .catch((error) => {
        perfLog('Procurement.Viewer', 'model-load-failed', {
          durationMs: Math.round(performance.now() - startedAt),
          error: error instanceof Error ? error.message : String(error),
        }, 'error');
        setLoadError('Failed to load the current model artifact.');
      });

    return () => {
      cancelled = true;
    };
  }, [getAccessToken, isActive, modelUrl, onModelLoaded, rebuildMeshIndex, requestRender, scheduleSelectionUpdate]);

  useEffect(() => {
    visualComponentsRef.current = visualComponents;
    const root = modelRef.current;
    if (!root) return;
    rebuildMeshIndex(root);
    scheduleSelectionUpdate(selectedVisualNodeIdsRef.current);
    requestRender();
  }, [rebuildMeshIndex, requestRender, scheduleSelectionUpdate, visualComponents]);

  useEffect(() => {
    selectedVisualNodeIdsRef.current = selectedVisualNodeIds;
    scheduleSelectionUpdate(selectedVisualNodeIds);
  }, [scheduleSelectionUpdate, selectedVisualNodeIds]);

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={containerRef} className="h-full min-h-[360px] w-full" />
      <div className="pointer-events-none absolute left-3 top-3 rounded bg-slate-950/80 px-3 py-2 text-xs text-slate-300">
        {loadError || statusText}
      </div>
      <div className={`pointer-events-none absolute right-3 top-3 max-w-[60%] rounded px-3 py-2 text-xs ${isVerified ? 'bg-emerald-950/80 text-emerald-200' : 'bg-red-950/80 text-red-200'}`}>
        {verificationText}
      </div>
      {!ENABLE_PROCUREMENT_3D_SELECTION && (
        <div className="pointer-events-none absolute right-3 top-14 max-w-[60%] rounded bg-slate-950/80 px-3 py-2 text-xs text-slate-400">
          3D selection paused for performance
        </div>
      )}
      <div className="absolute bottom-3 left-3 flex gap-2 text-[11px] text-slate-300">
        {([
          ['valid', 'BoM linked'],
          ['incomplete', 'Incomplete'],
          ['missing', 'Missing'],
          ['mesh', 'Mesh/face'],
        ] as Array<[CoverageState, string]>).map(([state, label]) => (
          <div key={state} className="rounded bg-slate-950/80 px-2 py-1">
            <span className={`mr-1 inline-block h-2 w-2 rounded-full ${coverageClass(state)}`} />
            {label}
          </div>
        ))}
      </div>
    </div>
  );
};

const SupplierEditor: React.FC<{
  supplier: Supplier;
  setSupplier: (supplier: Supplier) => void;
  onSave: () => void;
  onCancel: () => void;
}> = ({ supplier, setSupplier, onSave, onCancel }) => (
  <div className="rounded border border-slate-800 bg-slate-900 p-4">
    <div className="mb-4 text-sm font-bold text-amber-300">{supplier.name ? 'Edit Supplier' : 'New Supplier'}</div>
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {([
        ['name', 'Company name'],
        ['contact', 'Contact person'],
        ['phone', 'Phone'],
        ['email', 'Email'],
      ] as Array<[keyof Pick<Supplier, 'name' | 'contact' | 'phone' | 'email'>, string]>).map(([key, label]) => (
        <label key={key} className="text-xs text-slate-500">
          {label}
          <input value={supplier[key]} onChange={(event) => setSupplier({ ...supplier, [key]: event.target.value })} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100" />
        </label>
      ))}
    </div>
    <label className="mt-3 block text-xs text-slate-500">
      Notes
      <textarea value={supplier.notes} onChange={(event) => setSupplier({ ...supplier, notes: event.target.value })} className="mt-1 block h-20 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100" />
    </label>
    <div className="mt-4 flex gap-2">
      <button onClick={onSave} className="rounded bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-500">Save</button>
      <button onClick={onCancel} className="rounded bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700">Cancel</button>
    </div>
  </div>
);

const SupplierDetail: React.FC<{
  supplier: Supplier;
  suppliers: Supplier[];
  setSuppliers: React.Dispatch<React.SetStateAction<Supplier[]>>;
  onEdit: () => void;
  onDelete: () => void;
  editPricing: SupplierPricing | null;
  setEditPricing: (pricing: SupplierPricing | null) => void;
  sections: string[];
}> = ({ supplier, suppliers, setSuppliers, onEdit, onDelete, editPricing, setEditPricing, sections }) => {
  const savePricing = () => {
    if (!editPricing?.section.trim()) return;
    const nextPricing = supplier.pricing.some((price) => price.section === editPricing.section)
      ? supplier.pricing.map((price) => price.section === editPricing.section ? editPricing : price)
      : [...supplier.pricing, editPricing];
    const updated = { ...supplier, pricing: nextPricing };
    setSuppliers(suppliers.map((item) => item.id === supplier.id ? updated : item));
    setEditPricing(null);
  };

  const defaultSection = sections.find((section) => !supplier.pricing.some((price) => price.section === section)) || sections[0] || '';

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <div>
          <div className="text-lg font-bold text-slate-100">{supplier.name}</div>
          {supplier.contact && <div className="text-sm text-slate-400">{supplier.contact}</div>}
          {(supplier.phone || supplier.email) && <div className="mt-1 text-xs text-slate-500">{[supplier.phone, supplier.email].filter(Boolean).join(' | ')}</div>}
        </div>
        <div className="ml-auto flex gap-2">
          <button onClick={onEdit} className="rounded bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700">Edit</button>
          <button onClick={onDelete} className="rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300 hover:bg-red-950">Delete</button>
        </div>
      </div>

      <div className="rounded border border-slate-800 bg-slate-900 p-4">
        <div className="mb-3 text-sm font-bold text-amber-300">Pricing Rules</div>
        <table className="mb-4 w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="py-2">Requirement</th>
              <th className="py-2">Package</th>
              <th className="py-2">Stock/package</th>
              <th className="py-2 text-right">$ package</th>
              <th className="py-2 text-right">$ unit</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody>
            {supplier.pricing.map((price) => (
              <tr key={price.section} className="border-t border-slate-800">
                <td className="py-2 text-slate-200">{price.section}</td>
                <td className="py-2 text-slate-300">{price.package_type}</td>
                <td className="py-2 text-slate-300">
                  {price.package_type === 'stock_length' || price.package_type === 'roll' || price.package_type === 'reel'
                    ? `${price.stock_lengths.join(', ')}m`
                    : `${price.package_quantity || 1} ${price.package_unit}`}
                </td>
                <td className="py-2 text-right text-slate-200">{money(price.price_per_package)}</td>
                <td className="py-2 text-right text-slate-200">{money(price.price_per_unit)}</td>
                <td className="py-2 text-right">
                  <button onClick={() => setEditPricing({ ...price, stock_lengths: [...price.stock_lengths] })} className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300">Edit</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {editPricing ? (
          <PricingEditor pricing={editPricing} setPricing={setEditPricing} sections={sections} onSave={savePricing} onCancel={() => setEditPricing(null)} />
        ) : (
          <button
            onClick={() => setEditPricing({
              section: defaultSection,
              package_type: 'each',
              stock_lengths: [6, 9, 12],
              package_quantity: 1,
              package_unit: 'each',
              price_per_package: 0,
              price_per_unit: 0,
              notes: '',
            })}
            className="rounded bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700"
          >
            Add price rule
          </button>
        )}
      </div>
    </div>
  );
};

const PricingEditor: React.FC<{
  pricing: SupplierPricing;
  setPricing: (pricing: SupplierPricing) => void;
  sections: string[];
  onSave: () => void;
  onCancel: () => void;
}> = ({ pricing, setPricing, sections, onSave, onCancel }) => {
  const [stockText, setStockText] = useState(pricing.stock_lengths.join(', '));
  const parseStocks = (value: string) => value.split(/[,\s]+/).map(Number).filter((item) => item > 0);
  const usesLengths = pricing.package_type === 'stock_length' || pricing.package_type === 'roll' || pricing.package_type === 'reel';

  return (
    <div className="rounded border border-slate-700 bg-slate-950 p-3">
      <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4">
        {PACKAGE_TYPES.map((item) => (
          <button key={item} onClick={() => setPricing({ ...pricing, package_type: item })} className={`rounded border px-3 py-2 text-xs font-semibold ${pricing.package_type === item ? 'border-amber-500 bg-amber-600 text-white' : 'border-slate-800 bg-slate-900 text-slate-400'}`}>
            {item.replace(/_/g, ' ')}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <label className="text-xs text-slate-500">
          Requirement
          <input list="procurement-section-list" value={pricing.section} onChange={(event) => setPricing({ ...pricing, section: event.target.value })} className="mt-1 block w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100" />
          <datalist id="procurement-section-list">{sections.map((section) => <option key={section} value={section} />)}</datalist>
        </label>
        {usesLengths ? (
          <label className="text-xs text-slate-500">
            Stock lengths m
            <input value={stockText} onChange={(event) => { setStockText(event.target.value); setPricing({ ...pricing, stock_lengths: parseStocks(event.target.value) }); }} className="mt-1 block w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100" />
          </label>
        ) : (
          <label className="text-xs text-slate-500">
            Package quantity
            <input type="number" min="0" step="0.001" value={pricing.package_quantity} onChange={(event) => setPricing({ ...pricing, package_quantity: Number(event.target.value) || 0 })} className="mt-1 block w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100" />
          </label>
        )}
        <label className="text-xs text-slate-500">
          Package unit
          <input value={pricing.package_unit} onChange={(event) => setPricing({ ...pricing, package_unit: event.target.value })} className="mt-1 block w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100" />
        </label>
        <label className="text-xs text-slate-500">
          $/package ex GST
          <input type="number" min="0" step="0.01" value={pricing.price_per_package} onChange={(event) => setPricing({ ...pricing, price_per_package: Number(event.target.value) || 0 })} className="mt-1 block w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100" />
        </label>
        <label className="text-xs text-slate-500">
          $/unit or $/m ex GST
          <input type="number" min="0" step="0.01" value={pricing.price_per_unit} onChange={(event) => setPricing({ ...pricing, price_per_unit: Number(event.target.value) || 0 })} className="mt-1 block w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100" />
        </label>
        <label className="text-xs text-slate-500">
          Notes
          <input value={pricing.notes} onChange={(event) => setPricing({ ...pricing, notes: event.target.value })} className="mt-1 block w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100" />
        </label>
      </div>
      <div className="mt-3 flex gap-2">
        <button onClick={onSave} className="rounded bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-500">Save</button>
        <button onClick={onCancel} className="rounded bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700">Cancel</button>
      </div>
    </div>
  );
};
