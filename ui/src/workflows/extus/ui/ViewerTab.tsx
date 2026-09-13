import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { SpanStatusCode } from '@opentelemetry/api';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import * as BufferGeometryUtils from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import { MODEL_STATUS_POLL_INTERVAL_MS, getPollingDelay, shouldRunPollingRequest } from '../../shared/polling';
import { GuestWorkflowNotice } from '../../shared/ui/GuestWorkflowNotice';
import { startInteractionSpan } from '../../../telemetry';
import {
  SCENE_NODE_APPEARANCE_STORAGE_KEY,
  SCENE_NODE_SELECTION_STORAGE_KEY,
  SCENE_NODE_TARGET_EVENT,
  SCENE_NODE_TARGET_STORAGE_KEY,
  type SceneNodeAppearanceMap,
  createSceneNodeSelectionValue,
  getSceneNodePathKey,
  readSceneNodeAppearanceMap,
  resolveSceneNodeSelection,
} from '../../shared/sceneNodeSelection';
import type { ComponentPreviewImage } from '../../shared/componentPreview';
import {
  analyzePotentialCollisionsAsync,
  collisionDisplayLabel,
  collisionGroupFromLabel,
  type CollisionSourceReference,
  type PotentialCollision,
} from '../collisionAnalysis';
import {
  COLLISION_ENGINE_CHECKPOINT,
  verifyPotentialCollisions,
  type VerifiedCollisionAnalysisResult,
} from '../collisionNarrowPhase';
import type { StructuralViewerOverlay } from '../model/viewer';
import {
  createViewerMeshMaterials,
  DEFAULT_MODEL_COLOR,
  disposeMaterial,
  disposeMesh,
  disposeObjectTree,
  hasAuthoredMaterialColor,
  hasSourceMaterialTransparency,
  type ViewerMeshMaterials,
} from '../scene/materials';
import {
  buildViewerInstances,
  buildViewerBatch,
  applyViewerGeometryTransform,
  closestSelectableSceneNode,
  getRenderableObjectBounds,
  isViewerBatchMesh,
  isViewerObjectHidden,
  matchesExternalSelection,
  normalizeExternalSelectionId,
  resolveExternalSelectionMeshes,
} from '../scene/batching';
import { ViewerControls } from './ViewerControls';

export {
  DEFAULT_MODEL_COLOR,
  createViewerMeshMaterials,
  hasAuthoredMaterialColor,
} from '../scene/materials';
export { buildViewerBatch, isViewerObjectHidden } from '../scene/batching';
export type { StructuralViewerOverlay } from '../model/viewer';

interface ViewerProps {
  serverUrl: string;
  isActive?: boolean;
  statusTextOverride?: string;
  externalSelectedNodeIds?: string[];
  structuralOverlays?: StructuralViewerOverlay[];
  onStructuralRestraintSelect?: (restraintId: string) => void;
  onExternalSelectionPreviewChange?: (preview: ComponentPreviewImage | null) => void;
}

interface ModelViewerCanvasProps {
  modelUrl: string;
  getAccessToken: () => Promise<string>;
  statusText?: string;
  projectName?: string;
  isActive?: boolean;
  externalSelectedNodeIds?: string[];
  structuralOverlays?: StructuralViewerOverlay[];
  onStructuralRestraintSelect?: (restraintId: string) => void;
  onExternalSelectionPreviewChange?: (preview: ComponentPreviewImage | null) => void;
}

export function structuralCheckColor(
  status: StructuralViewerOverlay['status'],
): number {
  if (status === 'pass') return 0x22c55e;
  if (status === 'fail') return 0xef4444;
  return 0x94a3b8;
}

export function structuralRestraintColor(
  status: NonNullable<StructuralViewerOverlay['restraintSegments']>[number]['status'],
): number {
  if (status === 'verified') return 0x22c55e;
  if (status === 'candidate') return 0xf59e0b;
  if (status === 'missing' || status === 'inadequate') return 0xef4444;
  return 0x64748b;
}

export function structuralEvidenceColor(
  status: NonNullable<StructuralViewerOverlay['restraintMarkers']>[number]['evidenceStatus'],
): number {
  if (status === 'verified') return 0x22c55e;
  if (status === 'missing' || status === 'mismatch') return 0xef4444;
  return 0x94a3b8;
}

const COMPONENT_PREVIEW_SIZE = 512;
const STRUCTURAL_OVERLAY_NAME = 'TertiusStructuralMomentOverlay';

type GltfNodeJson = {
  children?: unknown;
  extras?: unknown;
  mesh?: unknown;
};

type GltfMeshJson = {
  name?: unknown;
  extras?: unknown;
};

type GltfSceneJson = {
  nodes?: unknown;
};

type GltfParserJson = {
  meshes?: unknown;
  nodes?: unknown;
  scenes?: unknown;
  scene?: unknown;
  extras?: unknown;
};

type GltfAssociation = {
  nodes?: number;
  meshes?: number;
  primitives?: number;
};

type GltfAssociationMap = Map<THREE.Object3D, GltfAssociation | undefined>;

export type ModelArtifactFormat = 'gltf' | 'stl';

export function detectModelArtifactFormat(contentType: string | null, buffer: ArrayBuffer): ModelArtifactFormat {
  const normalizedContentType = (contentType || '').toLowerCase();
  if (normalizedContentType.includes('stl')) return 'stl';
  if (normalizedContentType.includes('gltf') || normalizedContentType.includes('json')) return 'gltf';

  const bytes = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 256));
  if (bytes.length >= 4
    && bytes[0] === 0x67
    && bytes[1] === 0x6c
    && bytes[2] === 0x54
    && bytes[3] === 0x46) {
    return 'gltf';
  }
  const textPrefix = new TextDecoder().decode(bytes).trimStart();
  return textPrefix.startsWith('{') ? 'gltf' : 'stl';
}

export function annotateGltfNodeIds(
  root: THREE.Object3D,
  gltfJson: GltfParserJson | undefined,
  associations?: GltfAssociationMap,
): void {
  const meshes = Array.isArray(gltfJson?.meshes) ? gltfJson.meshes as GltfMeshJson[] : [];
  const nodes = Array.isArray(gltfJson?.nodes) ? gltfJson.nodes as GltfNodeJson[] : [];
  const scenes = Array.isArray(gltfJson?.scenes) ? gltfJson.scenes as GltfSceneJson[] : [];
  const sceneIndex = typeof gltfJson?.scene === 'number' ? gltfJson.scene : 0;
  const sceneNodeIds = Array.isArray(scenes[sceneIndex]?.nodes)
    ? scenes[sceneIndex].nodes.filter((value): value is number => Number.isInteger(value))
    : nodes
      .map((_, index) => index)
      .filter((index) => !nodes.some((node) => (
        Array.isArray(node.children) && node.children.some((childIndex) => childIndex === index)
      )));

  const rootExtras = gltfJson?.extras;
  if (rootExtras && typeof rootExtras === 'object') {
    const sourceMap = (rootExtras as { tertiusSourceMap?: unknown }).tertiusSourceMap;
    if (sourceMap && typeof sourceMap === 'object') root.userData.tertiusSourceMap = sourceMap;
  }

  const annotateNode = (object: THREE.Object3D | undefined, nodeId: number) => {
    if (!object || !nodes[nodeId]) return;
    object.userData.tertiusGltfNodeId = String(nodeId);
    const meshId = nodes[nodeId].mesh;
    const meshDefinition = typeof meshId === 'number' ? meshes[meshId] : undefined;
    if (
      typeof meshDefinition?.name === 'string'
      && meshDefinition.name.startsWith('__TERTIUS_COLLISION_IGNORE__ ')
    ) {
      object.userData.tertiusCollisionCheckDisabled = true;
    }
    if (typeof meshDefinition?.name === 'string') {
      const collisionGroup = collisionGroupFromLabel(meshDefinition.name);
      if (collisionGroup) object.userData.tertiusCollisionGroup = collisionGroup;
    }
    const meshExtras = meshDefinition?.extras;
    if (meshExtras && typeof meshExtras === 'object') {
      const meshBom = (meshExtras as { tertiusBom?: unknown }).tertiusBom;
      if (meshBom && typeof meshBom === 'object') object.userData.tertiusBom = meshBom;
    }
    const extras = nodes[nodeId].extras;
    if (extras && typeof extras === 'object') {
      const ids = (extras as { tertiusSourceCallIds?: unknown }).tertiusSourceCallIds;
      if (Array.isArray(ids)) object.userData.tertiusSourceCallIds = ids.map(String).filter(Boolean);
      const bom = (extras as { tertiusBom?: unknown }).tertiusBom;
      if (bom && typeof bom === 'object') object.userData.tertiusBom = bom;
    }
    object.name = collisionDisplayLabel(object.name);
    const childNodeIds = Array.isArray(nodes[nodeId].children)
      ? nodes[nodeId].children.filter((value): value is number => Number.isInteger(value))
      : [];
    childNodeIds.forEach((childNodeId, childIndex) => annotateNode(object.children[childIndex], childNodeId));
  };

  sceneNodeIds.forEach((nodeId, childIndex) => annotateNode(root.children[childIndex], nodeId));

  associations?.forEach((association, object) => {
    if (!association) return;
    if (typeof association.nodes === 'number') {
      object.userData.tertiusGltfNodeId = String(association.nodes);
    }
    if (typeof association.meshes === 'number') {
      object.userData.tertiusGltfMeshId = association.meshes;
      object.userData.tertiusGltfPrimitiveId = association.primitives ?? 0;
    }
  });
}

function preferredCollisionSource(references: CollisionSourceReference[]): CollisionSourceReference | undefined {
  return references.find(reference => reference.sourceFile && reference.sourceLine)
    || references.find(reference => reference.definitionFile && reference.definitionLine)
    || references[0];
}

function collisionSourceLabel(references: CollisionSourceReference[]): string {
  const reference = preferredCollisionSource(references);
  if (!reference) return 'Source mapping unavailable — recompile to add it';
  const file = reference.sourceFile || reference.definitionFile;
  const line = reference.sourceLine || reference.definitionLine;
  const location = file ? `${file}${line ? `:${line}` : ''}` : reference.callId;
  return reference.functionName ? `${location} · ${reference.functionName}()` : location;
}

function collisionClipboardText(pair: PotentialCollision): string {
  const contact = pair.contactPoint
    ? [pair.contactPoint.x, pair.contactPoint.y, pair.contactPoint.z]
      .map(value => (value * 1000).toFixed(1))
      .join(', ')
    : 'unavailable';
  return [
    'Investigate this verified rendered-mesh collision in design.py:',
    `A: ${pair.a.label} — ${collisionSourceLabel(pair.a.sourceReferences)}`,
    `B: ${pair.b.label} — ${collisionSourceLabel(pair.b.sourceReferences)}`,
    `Approximate surface contact in viewer coordinates: ${contact} mm.`,
    'The broad-phase box candidate was confirmed by triangle-to-triangle intersection.',
  ].join('\n');
}

type CollisionScanState = {
  phase: 'idle' | 'broad' | 'prepare' | 'narrow' | 'cancelled' | 'error';
  processed: number;
  total: number;
  message?: string;
};


export const ViewerTab: React.FC<ViewerProps> = (props) => {
  const { authMode, login } = useAuth();
  if (authMode === 'guest') {
    return (
      <GuestWorkflowNotice
        title="Log in to view compiled models"
        message="Extus loads authenticated model artifacts after Intus compilation."
        onLogin={login}
      />
    );
  }
  return <LatestModelViewer {...props} />;
};

export const LatestModelViewer: React.FC<ViewerProps> = ({
  serverUrl,
  isActive = true,
  statusTextOverride,
  externalSelectedNodeIds,
  structuralOverlays,
  onStructuralRestraintSelect,
  onExternalSelectionPreviewChange,
}) => {
  const { getAccessToken } = useAuth();
  const [statusText, setStatusText] = useState('Waiting for connection...');
  const [url, setUrl] = useState<string>('');
  const [projectName, setProjectName] = useState<string>('');

  // Poll for latest active-project model changes.
  useEffect(() => {
    if (!isActive) return;

    let mounted = true;
    let mtime = 0;

    const checkStatus = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const projRes = await apiFetch(`${serverUrl}/project_name`, getAccessToken);
        if (projRes.ok && mounted) {
          const pData = await projRes.json();
          if (pData.project_name) {
            setProjectName(pData.project_name);
          }
        }

        const res = await apiFetch(`${serverUrl}/status`, getAccessToken);
        if (res.ok) {
          const data = await res.json();
          if (data.mtime && data.mtime !== mtime) {
            if (mounted) {
              mtime = data.mtime;
              setUrl(`${serverUrl}/model?t=${data.mtime}`);
              setStatusText(`Model updated at ${new Date(data.mtime * 1000).toLocaleTimeString()}`);
            }
          }
        } else {
          if (mounted) setStatusText('No active model artifact found yet. Compile a project in Intus!');
        }
      } catch (e) {
        if (mounted) setStatusText('Lost connection to file server.');
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, getPollingDelay(MODEL_STATUS_POLL_INTERVAL_MS));

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [serverUrl, isActive, getAccessToken]);

  return (
    <ModelViewerCanvas
      modelUrl={url}
      getAccessToken={getAccessToken}
      statusText={statusTextOverride || statusText}
      projectName={projectName}
      isActive={isActive}
      externalSelectedNodeIds={externalSelectedNodeIds}
      structuralOverlays={structuralOverlays}
      onStructuralRestraintSelect={onStructuralRestraintSelect}
      onExternalSelectionPreviewChange={onExternalSelectionPreviewChange}
    />
  );
};

export const ModelViewerCanvas: React.FC<ModelViewerCanvasProps> = ({
  modelUrl,
  getAccessToken,
  statusText = 'Waiting for model...',
  projectName = '',
  isActive = true,
  externalSelectedNodeIds,
  structuralOverlays,
  onStructuralRestraintSelect,
  onExternalSelectionPreviewChange,
}) => {
  const [showGrid, setShowGrid] = useState<boolean>(true);
  const [autoRotate, setAutoRotate] = useState<boolean>(false);
  const [renderQuality, setRenderQuality] = useState<'high' | 'low'>('high');
  const [loadErrorText, setLoadErrorText] = useState<string | null>(null);
  const [isModelLoading, setIsModelLoading] = useState<boolean>(false);
  const [viewerInstancingStats, setViewerInstancingStats] = useState<{
    batches: number;
    instances: number;
    fallbackMeshes: number;
  }>();
  
  const [sceneGraph, setSceneGraph] = useState<THREE.Object3D | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [collisionPanelOpen, setCollisionPanelOpen] = useState(false);
  const [collisionAnalysis, setCollisionAnalysis] = useState<VerifiedCollisionAnalysisResult | null>(null);
  const [collisionScanState, setCollisionScanState] = useState<CollisionScanState>({
    phase: 'idle',
    processed: 0,
    total: 0,
  });
  const collisionScanRunning = collisionScanState.phase === 'broad'
    || collisionScanState.phase === 'prepare'
    || collisionScanState.phase === 'narrow';
  const [collisionMinimumPenetration, setCollisionMinimumPenetration] = useState(1);
  const [collisionSearch, setCollisionSearch] = useState('');
  const [collisionVisibleLimit, setCollisionVisibleLimit] = useState(50);
  const [activeCollisionId, setActiveCollisionId] = useState<string | null>(null);
  const [copiedCollisionId, setCopiedCollisionId] = useState<string | null>(null);
  const [appearanceByPath, setAppearanceByPath] = useState<SceneNodeAppearanceMap>(() => (
    readSceneNodeAppearanceMap(localStorage.getItem(SCENE_NODE_APPEARANCE_STORAGE_KEY))
  ));
  
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const autoRotateRef = useRef<boolean>(false);
  const renderQualityRef = useRef<'high' | 'low'>('high');
  const needsRenderRef = useRef<boolean>(true);
  const isActiveRef = useRef<boolean>(isActive);
  const structuralRestraintSelectRef = useRef(onStructuralRestraintSelect);

  useEffect(() => {
    structuralRestraintSelectRef.current = onStructuralRestraintSelect;
  }, [onStructuralRestraintSelect]);
  
  // THREE.js refs
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const meshRef = useRef<THREE.Object3D | null>(null);
  const animIdRef = useRef<number>(0);
  const modelLoadRequestRef = useRef<number>(0);
  const loadedModelUrlRef = useRef<string>('');
  const previousExternalSelectionKeyRef = useRef<string>('');
  const appearanceByPathRef = useRef(appearanceByPath);
  const collisionScanAbortRef = useRef<AbortController | null>(null);
  const collisionScanRunningRef = useRef(false);

  const filteredCollisionPairs = useMemo(() => {
    const pairs = collisionAnalysis?.pairs ?? [];
    const query = collisionSearch.trim().toLocaleLowerCase();
    if (!query) return pairs;
    return pairs.filter(pair => [
      pair.a.label,
      pair.b.label,
      collisionSourceLabel(pair.a.sourceReferences),
      collisionSourceLabel(pair.b.sourceReferences),
    ].some(value => value.toLocaleLowerCase().includes(query)));
  }, [collisionAnalysis, collisionSearch]);
  const visibleCollisionPairs = filteredCollisionPairs.slice(0, collisionVisibleLimit);

  useEffect(() => {
    appearanceByPathRef.current = appearanceByPath;
  }, [appearanceByPath]);

  useEffect(() => {
    collisionScanRunningRef.current = collisionScanRunning;
    if (controlsRef.current) {
      controlsRef.current.autoRotate = autoRotateRef.current && !collisionScanRunning;
    }
  }, [collisionScanRunning]);

  const clearCurrentModel = useCallback(() => {
    collisionScanAbortRef.current?.abort();
    collisionScanAbortRef.current = null;
    const scene = sceneRef.current;
    const current = meshRef.current;
    setViewerInstancingStats(undefined);
    if (!scene || !current) return;
    disposeObjectTree(current);
    scene.remove(current);
    meshRef.current = null;
    setSceneGraph(null);
    setSelectedNodeId(null);
    setCollisionAnalysis(null);
    setCollisionVisibleLimit(50);
    setCollisionScanState({ phase: 'idle', processed: 0, total: 0 });
    setActiveCollisionId(null);
  }, []);

  const resizeRendererToContainer = useCallback(() => {
    const container = containerRef.current;
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    if (!container || !renderer || !camera) return;

    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    needsRenderRef.current = true;
  }, []);

  const frameCameraOnBox = useCallback((box: THREE.Box3, padding = 1.08) => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || box.isEmpty()) return false;

    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const radius = Math.max(sphere.radius, 0.0001);
    const fov = THREE.MathUtils.degToRad(camera.fov);
    const distance = (radius / Math.sin(fov / 2)) * padding;
    const currentDirection = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
    if (currentDirection.lengthSq() === 0) currentDirection.set(1, 1, 0.7).normalize();

    camera.position.copy(sphere.center).addScaledVector(currentDirection, distance);
    camera.near = Math.max(0.00001, radius / 100, distance / 10_000);
    camera.far = Math.max(distance * 40, radius * 80, camera.near * 1000);
    camera.updateProjectionMatrix();
    controls.minDistance = Math.max(0.00001, radius * 0.05);
    controls.maxDistance = Math.max(distance * 200, radius * 500, controls.minDistance * 1000);
    controls.target.copy(sphere.center);
    controls.update();
    needsRenderRef.current = true;
    return true;
  }, []);

  const frameModelRoot = useCallback((padding = 1.5) => {
    const model = meshRef.current;
    if (!model) return false;
    const box = getRenderableObjectBounds(model);
    return frameCameraOnBox(box, padding);
  }, [frameCameraOnBox]);

  // 1. Initialize Scene while the viewer tab is active.
  useEffect(() => {
    if (!isActive) return;
    if (!containerRef.current || !canvasRef.current) return;
    const container = containerRef.current;
    const canvas = canvasRef.current;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0f172a); // slate-900
    sceneRef.current = scene;

    const initialWidth = container.clientWidth || 1;
    const initialHeight = container.clientHeight || 1;
    const camera = new THREE.PerspectiveCamera(50, initialWidth / initialHeight, 0.1, 100000);
    camera.up.set(0, 0, 1);
    camera.position.set(200, 200, 200);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setSize(initialWidth, initialHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.35;
    rendererRef.current = renderer;

    const controls = new OrbitControls(camera, canvas);
    controlsRef.current = controls;
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.autoRotate = autoRotateRef.current;
    controls.autoRotateSpeed = 1.5;
    
    let resumeTimeout: ReturnType<typeof setTimeout> | null = null;
    const handleInteraction = () => {
      controls.autoRotate = false;
      if (resumeTimeout) clearTimeout(resumeTimeout);
      resumeTimeout = setTimeout(() => {
        controls.autoRotate = autoRotateRef.current;
      }, 5000);
    };

    controls.addEventListener('start', handleInteraction);
    canvas.addEventListener('mousedown', handleInteraction);
    canvas.addEventListener('wheel', handleInteraction);
    
    // Lighting setup
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.65);
    ambientLight.name = 'Ambient';
    scene.add(ambientLight);
    
    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.8);
    hemiLight.name = 'Hemi';
    hemiLight.position.set(0, 0, 200);
    scene.add(hemiLight);

    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(100, 100, 200);
    sun.castShadow = true;
    sun.shadow.mapSize.width = 2048;
    sun.shadow.mapSize.height = 2048;
    sun.shadow.bias = -0.0005;
    scene.add(sun);
    
    // Grid and Axes Helpers
    const gridHelper = new THREE.GridHelper(500, 50, 0x888888, 0x444444);
    gridHelper.rotation.x = Math.PI / 2; // Z-up orientation
    gridHelper.name = "GridHelper";
    scene.add(gridHelper);

    const axesHelper = new THREE.AxesHelper(100);
    axesHelper.name = "AxesHelper";
    scene.add(axesHelper);

    window.addEventListener('resize', resizeRendererToContainer);

    let lastRenderAt = 0;
    const animate = (timestamp = 0) => {
      animIdRef.current = requestAnimationFrame(animate);
      const controlsChanged = controls.update();
      if (!controls.autoRotate && !controlsChanged && !needsRenderRef.current) return;
      if (collisionScanRunningRef.current && timestamp - lastRenderAt < 100) return;
      lastRenderAt = timestamp;
      renderer.render(scene, camera);
      needsRenderRef.current = false;
    };
    animate();

    return () => {
      window.removeEventListener('resize', resizeRendererToContainer);
      controls.removeEventListener('start', handleInteraction);
      canvas.removeEventListener('mousedown', handleInteraction);
      canvas.removeEventListener('wheel', handleInteraction);
      if (resumeTimeout) clearTimeout(resumeTimeout);
      cancelAnimationFrame(animIdRef.current);
      animIdRef.current = 0;
      disposeObjectTree(scene);
      renderer.dispose();
      sceneRef.current = null;
      cameraRef.current = null;
      rendererRef.current = null;
      controlsRef.current = null;
      meshRef.current = null;
      loadedModelUrlRef.current = '';
      setSceneGraph(null);
      setSelectedNodeId(null);
    };
  }, [isActive, resizeRendererToContainer]);

  useEffect(() => {
    needsRenderRef.current = true;
  });

  useEffect(() => {
    isActiveRef.current = isActive;
    if (controlsRef.current) controlsRef.current.enabled = isActive;
    if (!isActive) return;

    resizeRendererToContainer();
    const frame = requestAnimationFrame(resizeRendererToContainer);
    return () => cancelAnimationFrame(frame);
  }, [isActive, resizeRendererToContainer]);

  useEffect(() => {
    if (!isActive || !containerRef.current) return;
    const container = containerRef.current;
    if (typeof ResizeObserver === 'undefined') {
      resizeRendererToContainer();
      return;
    }
    const resizeObserver = new ResizeObserver(resizeRendererToContainer);
    resizeObserver.observe(container);
    resizeRendererToContainer();
    return () => resizeObserver.disconnect();
  }, [isActive, resizeRendererToContainer]);

  useEffect(() => {
    if (!sceneRef.current) return;
    const grid = sceneRef.current.getObjectByName("GridHelper");
    const axes = sceneRef.current.getObjectByName("AxesHelper");
    if (grid) grid.visible = showGrid;
    if (axes) axes.visible = showGrid;
  }, [showGrid]);

  useEffect(() => {
    autoRotateRef.current = autoRotate;
    if (controlsRef.current) {
       controlsRef.current.autoRotate = autoRotate && !collisionScanRunning;
    }
  }, [autoRotate, collisionScanRunning]);

  useEffect(() => {
    if (!rendererRef.current || !sceneRef.current) return;
    const isHigh = renderQuality === 'high';
    renderQualityRef.current = renderQuality;
    
    rendererRef.current.shadowMap.enabled = isHigh;
    rendererRef.current.setPixelRatio(isHigh ? Math.min(window.devicePixelRatio, 2) : 1);
    
    sceneRef.current.traverse((node) => {
      if ((node as THREE.Light).isLight) {
         node.castShadow = isHigh && node.name !== 'Ambient' && node.name !== 'Hemi';
      }
      if ((node as THREE.Mesh).isMesh) {
         node.castShadow = isHigh;
         node.receiveShadow = isHigh;
         if ((node as THREE.Mesh).material) {
            const material = (node as THREE.Mesh).material;
            (Array.isArray(material) ? material : [material]).forEach((item) => {
              item.needsUpdate = true;
            });
         }
      }
    });
    needsRenderRef.current = true;
  }, [renderQuality]);

  // 3. Load GLTF when URL changes
  useEffect(() => {
    const requestId = modelLoadRequestRef.current + 1;
    modelLoadRequestRef.current = requestId;
    setLoadErrorText(null);
    if (!isActive || !modelUrl || !sceneRef.current) {
      if (!modelUrl) {
        loadedModelUrlRef.current = '';
        clearCurrentModel();
      }
      setIsModelLoading(false);
      return;
    }

    const isNewModelUrl = modelUrl !== loadedModelUrlRef.current;
    if (isNewModelUrl) {
      clearCurrentModel();
    }
    setIsModelLoading(true);
    
    let isCancelled = false;
    let loadSpanEnded = false;
    const isCurrentRequest = () => !isCancelled && modelLoadRequestRef.current === requestId;
    const loadSpan = startInteractionSpan('3d_viewer_load', {
      workflow: 'extus',
      render_quality: renderQualityRef.current,
    });
    const gltfLoader = new GLTFLoader();
    const stlLoader = new STLLoader();

    const finishLoad = () => {
      if (isCurrentRequest()) {
        setIsModelLoading(false);
      }
      if (!loadSpanEnded) {
        loadSpan.end();
        loadSpanEnded = true;
      }
    };

    const failLoad = (message: string, err?: unknown) => {
      if (!isCurrentRequest()) return;
      if (err) console.error(message, err);
      loadSpan.setStatus({ code: SpanStatusCode.ERROR });
      loadSpan.addEvent('exception', {
        'exception.type': err instanceof Error ? err.name : typeof err,
        'error.source': '3d_viewer_load',
      });
      setLoadErrorText(message);
      loadedModelUrlRef.current = '';
      clearCurrentModel();
      finishLoad();
    };
    
    const yieldToBrowser = () => new Promise<void>((resolve) => {
      window.setTimeout(resolve, 0);
    });

    const acceptModel = async (
      model: THREE.Object3D,
      gltfJson?: GltfParserJson,
      gltfAssociations?: GltfAssociationMap,
    ) => {
      if (!isCurrentRequest()) return;
      if (gltfJson) annotateGltfNodeIds(model, gltfJson, gltfAssociations);

      // Compute bounding box and center
      const box = new THREE.Box3().setFromObject(model);
      const center = new THREE.Vector3();
      box.getCenter(center);
      model.position.sub(center);

      // GLTF is Y-up; STL emitted by the CAD compiler is already Z-up.
      if (gltfJson) model.rotation.x = Math.PI / 2;

      // Update camera
      if (cameraRef.current) {
         const camera = cameraRef.current;
         const sphere = box.getBoundingSphere(new THREE.Sphere());
         const fov = camera.fov * (Math.PI / 180);
         let distance = Math.abs(sphere.radius / Math.sin(fov / 2));
         distance *= 1.5; // Padding

         const currentDir = new THREE.Vector3().subVectors(camera.position, new THREE.Vector3(0,0,0)).normalize();
         if (currentDir.lengthSq() === 0) currentDir.set(1, 1, 1).normalize();

         camera.position.copy(currentDir.multiplyScalar(distance));
         camera.lookAt(0, 0, 0);
         camera.updateProjectionMatrix();

         // Update helpers
         const size = Math.max(500, Math.ceil(sphere.radius * 4));
         const grid = sceneRef.current!.getObjectByName("GridHelper");
         if (grid) {
           const scale = size / 500;
           grid.scale.set(scale, scale, scale);
         }
         const axes = sceneRef.current!.getObjectByName("AxesHelper");
         if (axes) {
           const scale = size / 200;
           axes.scale.set(scale, scale, scale);
         }
      }

      // Override materials to add shadows and default color
      const sharedMaterial = new THREE.MeshStandardMaterial({
        color: DEFAULT_MODEL_COLOR, // Steel blueish
        metalness: 0.15,
        roughness: 0.72,
        side: THREE.FrontSide // FrontSide doubles rendering performance over DoubleSide
      });

      const highlightMaterial = sharedMaterial.clone();
      highlightMaterial.emissive.setHex(0x3b82f6);
      highlightMaterial.emissiveIntensity = 0.5;
      highlightMaterial.polygonOffset = true;
      highlightMaterial.polygonOffsetFactor = -1;
      highlightMaterial.polygonOffsetUnits = -1;

      model.userData.sharedMat = sharedMaterial;
      model.userData.highlightMat = highlightMaterial;

      const isHigh = renderQualityRef.current === 'high';

      model.updateMatrixWorld(true);
      const inverseModelMatrix = model.matrixWorld.clone().invert();
      const instanceCandidates: Array<{
        source: THREE.Mesh;
        geometry: THREE.BufferGeometry;
        sourceMaterial: THREE.Material | THREE.Material[];
        matrix: THREE.Matrix4;
        geometryKey?: string;
      }> = [];

      model.traverse((child) => {
        if ((child as THREE.Mesh).isMesh) {
           const mesh = child as THREE.Mesh;
           const relativeMatrix = new THREE.Matrix4().multiplyMatrices(inverseModelMatrix, mesh.matrixWorld);
           mesh.userData.viewerSourceMaterial = mesh.material;
           mesh.userData.viewerBatchMatrix = relativeMatrix;
           if (!hasSourceMaterialTransparency(mesh.material)) {
             const meshId = mesh.userData.tertiusGltfMeshId;
             const primitiveId = mesh.userData.tertiusGltfPrimitiveId;
             const supportsGpuInstances = !(mesh as THREE.SkinnedMesh).isSkinnedMesh
               && Object.keys(mesh.geometry.morphAttributes).length === 0;
             instanceCandidates.push({
               source: mesh,
               geometry: mesh.geometry,
               sourceMaterial: mesh.material,
               matrix: relativeMatrix,
               geometryKey: supportsGpuInstances && typeof meshId === 'number'
                 ? `mesh:${meshId}:primitive:${typeof primitiveId === 'number' ? primitiveId : 0}`
                 : undefined,
             });
           }

           mesh.visible = false; // Hidden by default, batched mesh handles rendering
           mesh.castShadow = false;
           mesh.receiveShadow = false;
        }
      });

      const viewerInstances = buildViewerInstances(instanceCandidates, {
        createMesh: (geometry, sourceMaterial, count) => {
          const materials = createViewerMeshMaterials(sourceMaterial, sharedMaterial);
          const mesh = new THREE.InstancedMesh(geometry, materials.base, count);
          mesh.userData.viewerMaterials = materials;
          return mesh;
        },
      });
      viewerInstances.meshes.forEach((instanceMesh) => {
        instanceMesh.castShadow = isHigh;
        instanceMesh.receiveShadow = isHigh;
        model.add(instanceMesh);
      });
      model.userData.instancedMeshes = viewerInstances.meshes;
      model.userData.viewerInstanceStats = {
        batches: viewerInstances.meshes.length,
        instances: viewerInstances.instanceCount,
        fallbackMeshes: viewerInstances.leftovers.length,
      };

      const sourceMeshes = viewerInstances.leftovers.map((candidate) => {
        const geometry = candidate.geometry.clone();
        applyViewerGeometryTransform(geometry, candidate.matrix);
        candidate.source.userData.viewerBatchGeometry = geometry;
        return new THREE.Mesh(geometry, candidate.sourceMaterial);
      });

      // Let Firefox paint the loading state before starting the expensive
      // geometry merges. Each later chunk yields for the same reason.
      await yieldToBrowser();
      if (!isCurrentRequest()) {
        disposeObjectTree(model);
        return;
      }

      if (sourceMeshes.length > 0) {
        try {
          // Chunk the geometry merge to prevent V8 Out of Memory crashes on massive assemblies
          const CHUNK_SIZE = 500;
          const chunks: THREE.BufferGeometry[] = [];
          const hasAuthoredColors = sourceMeshes.some(mesh => hasAuthoredMaterialColor(mesh.material));

          for (let i = 0; i < sourceMeshes.length; i += CHUNK_SIZE) {
             const batch = buildViewerBatch(sourceMeshes.slice(i, i + CHUNK_SIZE), { useAuthoredColors: hasAuthoredColors });
             if (batch) {
               chunks.push(batch.mesh.geometry);
               if (Array.isArray(batch.mesh.material)) batch.mesh.material.forEach(mat => mat.dispose());
               else batch.mesh.material.dispose();
             }
             await yieldToBrowser();
             if (!isCurrentRequest()) {
               chunks.forEach(g => g.dispose());
               disposeObjectTree(model);
               return;
             }
          }

          const finalMergedGeom = BufferGeometryUtils.mergeGeometries(chunks, false);
          chunks.forEach(g => g.dispose()); // Free intermediate chunks

          if (finalMergedGeom) {
             const batchedMaterial = hasAuthoredColors
               ? new THREE.MeshStandardMaterial({
                   color: 0xffffff,
                   vertexColors: true,
                   metalness: 0.15,
                   roughness: 0.72,
                   side: THREE.FrontSide
                 })
               : sharedMaterial;
             const batchedMesh = new THREE.Mesh(finalMergedGeom, batchedMaterial);
             batchedMesh.name = "TertiusBatchedMesh";
             batchedMesh.castShadow = isHigh;
             batchedMesh.receiveShadow = isHigh;
             model.add(batchedMesh);
             model.userData.batchedMesh = batchedMesh;
          }
        } catch (e) {
          console.error("BufferGeometryUtils.mergeGeometries chunking failed:", e);
        }
      }

      clearCurrentModel();

      sceneRef.current!.add(model);
      meshRef.current = model;
      loadedModelUrlRef.current = modelUrl;
      setViewerInstancingStats(model.userData.viewerInstanceStats);

      // Unpack the hierarchy
      setSceneGraph(model);
      setSelectedNodeId(null);
      finishLoad();
    };

    apiFetch(modelUrl, getAccessToken)
      .then(res => {
        if (!res.ok) {
          throw new Error(`Model artifact unavailable (${res.status || 'HTTP error'})`);
        }
        return Promise.all([res.arrayBuffer(), Promise.resolve(res.headers.get('content-type'))]);
      })
      .then(([buffer, contentType]) => {
        if (!isCurrentRequest()) return;
        if (detectModelArtifactFormat(contentType, buffer) === 'stl') {
          const geometry = stlLoader.parse(buffer);
          geometry.computeVertexNormals();
          const model = new THREE.Group();
          model.name = 'STL Model';
          model.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: DEFAULT_MODEL_COLOR })));
          void acceptModel(model).catch(err => {
            failLoad("Model artifact could not be prepared.", err);
          });
          return;
        }
        gltfLoader.parse(buffer, '', (gltf) => {
          const parser = gltf.parser as unknown as {
            json?: GltfParserJson;
            associations?: GltfAssociationMap;
          } | undefined;
          void acceptModel(gltf.scene, parser?.json || {}, parser?.associations).catch(err => {
            failLoad("Model artifact could not be prepared.", err);
          });
        }, (err) => {
          failLoad("Model artifact could not be parsed.", err);
        });
      })
      .catch(err => {
        failLoad(err instanceof Error ? err.message : "Model artifact could not be loaded.", err);
      });
      
    return () => {
      isCancelled = true;
      if (!loadSpanEnded) {
        loadSpan.end();
        loadSpanEnded = true;
      }
    };
  }, [modelUrl, getAccessToken, clearCurrentModel, isActive]);

  const externalSelectionKey = externalSelectedNodeIds?.join('\u001f') || '';

  const captureExternalSelectionPreview = useCallback((): ComponentPreviewImage | null => {
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    const model = meshRef.current;
    const sourceCamera = cameraRef.current;
    const controls = controlsRef.current;
    const selectedIds = new Set((externalSelectedNodeIds || []).filter(Boolean));
    if (!renderer || !scene || !model || !sourceCamera || selectedIds.size === 0) return null;
    const normalizedSelectedIds = new Set([...selectedIds].map(normalizeExternalSelectionId).filter(Boolean));
    const selection = resolveExternalSelectionMeshes(model, selectedIds);
    if (!selection.hasSelection) return null;

    let selectedObject: THREE.Object3D | null = null;
    model.traverse((object) => {
      if (selectedObject) return;
      if (matchesExternalSelection(object, selectedIds, normalizedSelectedIds)) selectedObject = object;
    });
    const previewObject = selectedObject as THREE.Object3D | null;
    if (!previewObject) return null;

    const box = selection.focusBounds;
    if (box.isEmpty()) return null;

    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const radius = Math.max(sphere.radius, 0.0001);
    const previewCamera = new THREE.PerspectiveCamera(38, 1, 0.1, 100_000);
    previewCamera.up.copy(sourceCamera.up);
    const fov = THREE.MathUtils.degToRad(previewCamera.fov);
    const distance = (radius / Math.sin(fov / 2)) * 1.08;
    const direction = controls
      ? new THREE.Vector3().subVectors(sourceCamera.position, controls.target).normalize()
      : new THREE.Vector3().subVectors(sourceCamera.position, sphere.center).normalize();
    if (direction.lengthSq() === 0) direction.set(1, 1, 0.7).normalize();
    previewCamera.position.copy(sphere.center).addScaledVector(direction, distance);
    previewCamera.near = Math.max(0.00001, radius / 100, distance / 10_000);
    previewCamera.far = Math.max(distance * 40, radius * 80, previewCamera.near * 1000);
    previewCamera.lookAt(sphere.center);
    previewCamera.updateProjectionMatrix();

    const renderTarget = new THREE.WebGLRenderTarget(COMPONENT_PREVIEW_SIZE, COMPONENT_PREVIEW_SIZE, {
      depthBuffer: true,
      stencilBuffer: false,
    });
    const previousTarget = renderer.getRenderTarget();
    const previousViewport = renderer.getViewport(new THREE.Vector4());
    const previousScissor = renderer.getScissor(new THREE.Vector4());
    const previousScissorTest = renderer.getScissorTest();
    const previousAutoClear = renderer.autoClear;
    const previousGridVisible = scene.getObjectByName('GridHelper')?.visible;
    const previousAxesVisible = scene.getObjectByName('AxesHelper')?.visible;

    try {
      const grid = scene.getObjectByName('GridHelper');
      const axes = scene.getObjectByName('AxesHelper');
      if (grid) grid.visible = false;
      if (axes) axes.visible = false;
      renderer.autoClear = true;
      renderer.setRenderTarget(renderTarget);
      renderer.setViewport(0, 0, COMPONENT_PREVIEW_SIZE, COMPONENT_PREVIEW_SIZE);
      renderer.setScissor(0, 0, COMPONENT_PREVIEW_SIZE, COMPONENT_PREVIEW_SIZE);
      renderer.setScissorTest(false);
      renderer.clear();
      renderer.render(scene, previewCamera);

      const pixels = new Uint8Array(COMPONENT_PREVIEW_SIZE * COMPONENT_PREVIEW_SIZE * 4);
      renderer.readRenderTargetPixels(renderTarget, 0, 0, COMPONENT_PREVIEW_SIZE, COMPONENT_PREVIEW_SIZE, pixels);
      const canvas = document.createElement('canvas');
      canvas.width = COMPONENT_PREVIEW_SIZE;
      canvas.height = COMPONENT_PREVIEW_SIZE;
      const context = canvas.getContext('2d');
      if (!context) return null;
      const imageData = context.createImageData(COMPONENT_PREVIEW_SIZE, COMPONENT_PREVIEW_SIZE);
      const rowBytes = COMPONENT_PREVIEW_SIZE * 4;
      for (let row = 0; row < COMPONENT_PREVIEW_SIZE; row += 1) {
        const sourceStart = row * rowBytes;
        const targetStart = (COMPONENT_PREVIEW_SIZE - row - 1) * rowBytes;
        imageData.data.set(pixels.subarray(sourceStart, sourceStart + rowBytes), targetStart);
      }
      context.putImageData(imageData, 0, 0);
      const requestedVisualNodeId = externalSelectedNodeIds?.find(Boolean) || previewObject.name || previewObject.uuid;
      return {
        dataUrl: canvas.toDataURL('image/png'),
        label: previewObject.name || previewObject.uuid,
        visualNodeId: requestedVisualNodeId,
        capturedAt: Date.now(),
      };
    } catch (error) {
      console.warn('Component preview capture failed', error);
      return null;
    } finally {
      const grid = scene.getObjectByName('GridHelper');
      const axes = scene.getObjectByName('AxesHelper');
      if (grid && typeof previousGridVisible === 'boolean') grid.visible = previousGridVisible;
      if (axes && typeof previousAxesVisible === 'boolean') axes.visible = previousAxesVisible;
      renderer.autoClear = previousAutoClear;
      renderer.setRenderTarget(previousTarget);
      renderer.setViewport(previousViewport.x, previousViewport.y, previousViewport.z, previousViewport.w);
      renderer.setScissor(previousScissor.x, previousScissor.y, previousScissor.z, previousScissor.w);
      renderer.setScissorTest(previousScissorTest);
      renderTarget.dispose();
    }
  }, [externalSelectedNodeIds]);

  useEffect(() => {
    const hadExternalSelection = Boolean(previousExternalSelectionKeyRef.current);
    previousExternalSelectionKeyRef.current = externalSelectionKey;

    if (!externalSelectionKey) {
      if (hadExternalSelection) frameModelRoot(1.5);
      return;
    }

    if (!externalSelectedNodeIds?.length || !meshRef.current) return;

    const selectedIds = new Set(externalSelectedNodeIds.filter(Boolean));
    if (selectedIds.size === 0) return;

    const model = meshRef.current;
    const selection = resolveExternalSelectionMeshes(model, selectedIds);
    if (!selection.hasSelection) return;

    frameCameraOnBox(selection.focusBounds, 1.08);
  }, [externalSelectedNodeIds, externalSelectionKey, frameCameraOnBox, frameModelRoot, sceneGraph]);

  useEffect(() => {
    const model = meshRef.current;
    if (!model) return;

    const previous = model.getObjectByName(STRUCTURAL_OVERLAY_NAME);
    if (previous) {
      model.remove(previous);
      disposeObjectTree(previous);
    }
    if (!structuralOverlays?.length) return;

    // Build123D source coordinates are Z-up. GLTF stores them Y-up and the
    // viewer rotates the loaded model back to Z-up, so overlay points are
    // authored in the model's pre-rotation coordinate system.
    const toModelCoordinates = (point: THREE.Vector3) => (
      new THREE.Vector3(point.x, point.z, -point.y)
    );
    const group = new THREE.Group();
    group.name = STRUCTURAL_OVERLAY_NAME;
    group.userData.tertiusStructuralOverlay = true;
    const loadArrowPeak = Math.max(
      Number.EPSILON,
      ...structuralOverlays.flatMap((overlay) => (
        (overlay.loadArrows ?? []).map(({ force_kN: force }) => (
          Math.hypot(force.x, force.y, force.z)
        ))
      )),
    );
    const diagnosticNodes = structuralOverlays.flatMap(
      (overlay) => overlay.nodes ?? [],
    );
    const reactionArrows = structuralOverlays.flatMap(
      (overlay) => overlay.reactions ?? [],
    );
    const reactionArrowPeak = Math.max(
      Number.EPSILON,
      ...reactionArrows.map(({ force_kN: force }) => (
        Math.hypot(force.x, force.y, force.z)
      )),
    );
    const restraintDemandMarkers = structuralOverlays.flatMap(
      (overlay) => overlay.restraintMarkers ?? [],
    );
    const restraintDemandPeak = Math.max(
      Number.EPSILON,
      ...restraintDemandMarkers.map((marker) => marker.requiredForceKN ?? 0),
    );

    for (const node of diagnosticNodes) {
      const nodeGroup = new THREE.Group();
      nodeGroup.name = `${STRUCTURAL_OVERLAY_NAME}Node-${node.id}`;
      nodeGroup.position.copy(toModelCoordinates(new THREE.Vector3(
        node.position.x,
        node.position.y,
        node.position.z,
      )));
      const nodeMesh = new THREE.Mesh(
        new THREE.SphereGeometry(node.restrained ? 0.022 : 0.014, 12, 8),
        new THREE.MeshBasicMaterial({
          color: node.restrained ? 0xf59e0b : 0x22d3ee,
          depthTest: false,
          transparent: true,
          opacity: 0.96,
        }),
      );
      nodeMesh.renderOrder = 35;
      nodeMesh.userData.tertiusStructuralOverlay = true;
      nodeGroup.add(nodeMesh);
      if (node.restrained) {
        const support = new THREE.Mesh(
          new THREE.ConeGeometry(0.038, 0.055, 4),
          new THREE.MeshBasicMaterial({
            color: 0xf59e0b,
            wireframe: true,
            depthTest: false,
            transparent: true,
            opacity: 0.95,
          }),
        );
        support.position.y = -0.04;
        support.renderOrder = 35;
        support.userData.tertiusStructuralOverlay = true;
        nodeGroup.add(support);
      }
      group.add(nodeGroup);
    }

    for (const reaction of reactionArrows) {
      const sourceForce = new THREE.Vector3(
        reaction.force_kN.x,
        reaction.force_kN.y,
        reaction.force_kN.z,
      );
      const magnitude = sourceForce.length();
      if (magnitude <= Number.EPSILON) continue;
      const length = 0.10 + 0.22 * Math.min(1, magnitude / reactionArrowPeak);
      const arrow = new THREE.ArrowHelper(
        toModelCoordinates(sourceForce).normalize(),
        toModelCoordinates(new THREE.Vector3(
          reaction.position.x,
          reaction.position.y,
          reaction.position.z,
        )),
        length,
        0xf472b6,
        Math.min(0.07, length * 0.35),
        Math.min(0.04, length * 0.2),
      );
      arrow.name = `${STRUCTURAL_OVERLAY_NAME}Reaction-${reaction.id}`;
      arrow.renderOrder = 34;
      arrow.traverse((object) => {
        object.userData.tertiusStructuralOverlay = true;
        object.renderOrder = 34;
        const material = (object as THREE.Mesh | THREE.Line).material;
        const materials = Array.isArray(material) ? material : material ? [material] : [];
        for (const candidate of materials) {
          candidate.depthTest = false;
          candidate.transparent = true;
          candidate.opacity = 0.95;
        }
      });
      group.add(arrow);
    }

    for (const structuralOverlay of structuralOverlays) {
      const memberGroup = new THREE.Group();
      memberGroup.name = `${STRUCTURAL_OVERLAY_NAME}-${structuralOverlay.id}`;
      memberGroup.userData.tertiusStructuralOverlay = true;
      const overlayMode = structuralOverlay.mode ?? 'moment';
      const statusColor = structuralCheckColor(structuralOverlay.status);
      const diagramColor = structuralOverlay.diagramColor ?? statusColor;

      for (const restraint of structuralOverlay.restraintSegments ?? []) {
        const start = toModelCoordinates(new THREE.Vector3(
          restraint.start.x,
          restraint.start.y,
          restraint.start.z,
        ));
        const end = toModelCoordinates(new THREE.Vector3(
          restraint.end.x,
          restraint.end.y,
          restraint.end.z,
        ));
        const direction = end.clone().sub(start);
        const length = direction.length();
        if (length <= Number.EPSILON) continue;
        const color = structuralRestraintColor(restraint.status);
        const segment = new THREE.Mesh(
          new THREE.CylinderGeometry(
            restraint.selected ? 0.022 : 0.011,
            restraint.selected ? 0.022 : 0.011,
            length,
            10,
          ),
          new THREE.MeshBasicMaterial({
            color,
            transparent: true,
            opacity: restraint.status === 'not_required' ? 0.42 : 0.92,
            depthTest: false,
            depthWrite: false,
          }),
        );
        segment.position.copy(start.clone().add(end).multiplyScalar(0.5));
        segment.quaternion.setFromUnitVectors(
          new THREE.Vector3(0, 1, 0),
          direction.normalize(),
        );
        segment.name = `${STRUCTURAL_OVERLAY_NAME}Restraint-${restraint.id}`;
        segment.renderOrder = 36;
        segment.userData.tertiusStructuralOverlay = true;
        segment.userData.tertiusStructuralRestraint = restraint;
        memberGroup.add(segment);

        // Keep the rendered trace dimensionally quiet while providing a practical
        // pointer target at whole-building zoom. Opacity does not affect raycasting.
        const hitTarget = new THREE.Mesh(
          new THREE.CylinderGeometry(0.07, 0.07, length, 8),
          new THREE.MeshBasicMaterial({
            transparent: true,
            opacity: 0,
            depthTest: false,
            depthWrite: false,
          }),
        );
        hitTarget.position.copy(segment.position);
        hitTarget.quaternion.copy(segment.quaternion);
        hitTarget.name = `${STRUCTURAL_OVERLAY_NAME}RestraintHit-${restraint.id}`;
        hitTarget.userData.tertiusStructuralOverlay = true;
        hitTarget.userData.tertiusStructuralRestraint = restraint;
        memberGroup.add(hitTarget);

        for (const [suffix, point] of [['Start', start], ['End', end]] as const) {
          const marker = new THREE.Mesh(
            new THREE.OctahedronGeometry(0.025, 0),
            new THREE.MeshBasicMaterial({
              color,
              transparent: true,
              opacity: 0.95,
              depthTest: false,
            }),
          );
          marker.position.copy(point);
          marker.name = `${STRUCTURAL_OVERLAY_NAME}Restraint${suffix}-${restraint.id}`;
          marker.renderOrder = 37;
          marker.userData.tertiusStructuralOverlay = true;
          marker.userData.tertiusStructuralRestraint = restraint;
          memberGroup.add(marker);
        }
      }

      for (const demandMarker of structuralOverlay.restraintMarkers ?? []) {
        const origin = toModelCoordinates(new THREE.Vector3(
          demandMarker.position.x,
          demandMarker.position.y,
          demandMarker.position.z,
        ));
        const markerColor = structuralRestraintColor(demandMarker.status);
        const markerGroup = new THREE.Group();
        markerGroup.position.copy(origin);
        markerGroup.name = `${STRUCTURAL_OVERLAY_NAME}Demand-${demandMarker.id}`;
        markerGroup.userData.tertiusStructuralOverlay = true;
        markerGroup.userData.tertiusStructuralRestraint = { id: demandMarker.traceId };

        const core = new THREE.Mesh(
          new THREE.SphereGeometry(demandMarker.selected ? 0.035 : 0.027, 14, 10),
          new THREE.MeshBasicMaterial({
            color: markerColor,
            transparent: true,
            opacity: 0.96,
            depthTest: false,
          }),
        );
        core.renderOrder = 39;
        core.userData.tertiusStructuralOverlay = true;
        core.userData.tertiusStructuralRestraint = { id: demandMarker.traceId };
        markerGroup.add(core);

        if (demandMarker.evidenceStatus !== 'verified') {
          const evidenceRing = new THREE.Mesh(
            new THREE.TorusGeometry(0.047, 0.006, 8, 28),
            new THREE.MeshBasicMaterial({
              color: structuralEvidenceColor(demandMarker.evidenceStatus),
              transparent: true,
              opacity: 0.98,
              depthTest: false,
            }),
          );
          evidenceRing.renderOrder = 40;
          evidenceRing.userData.tertiusStructuralOverlay = true;
          evidenceRing.userData.tertiusStructuralRestraint = { id: demandMarker.traceId };
          markerGroup.add(evidenceRing);
        }

        if (demandMarker.selected) {
          const selectionRing = new THREE.Mesh(
            new THREE.TorusGeometry(0.061, 0.004, 8, 28),
            new THREE.MeshBasicMaterial({
              color: 0x22d3ee,
              transparent: true,
              opacity: 0.95,
              depthTest: false,
            }),
          );
          selectionRing.rotation.x = Math.PI / 2;
          selectionRing.renderOrder = 41;
          selectionRing.userData.tertiusStructuralOverlay = true;
          selectionRing.userData.tertiusStructuralRestraint = { id: demandMarker.traceId };
          markerGroup.add(selectionRing);
        }

        const hitTarget = new THREE.Mesh(
          new THREE.SphereGeometry(0.075, 10, 8),
          new THREE.MeshBasicMaterial({
            transparent: true,
            opacity: 0,
            depthTest: false,
            depthWrite: false,
          }),
        );
        hitTarget.userData.tertiusStructuralOverlay = true;
        hitTarget.userData.tertiusStructuralRestraint = { id: demandMarker.traceId };
        markerGroup.add(hitTarget);
        memberGroup.add(markerGroup);

        const requiredForceKN = demandMarker.requiredForceKN ?? 0;
        const sourceDirection = new THREE.Vector3(
          demandMarker.direction.x,
          demandMarker.direction.y,
          demandMarker.direction.z,
        );
        if (requiredForceKN > Number.EPSILON && sourceDirection.lengthSq() > 0) {
          const direction = toModelCoordinates(sourceDirection).normalize();
          const length = 0.10 + 0.20 * Math.min(1, requiredForceKN / restraintDemandPeak);
          const arrow = new THREE.ArrowHelper(
            direction,
            origin,
            length,
            0x22d3ee,
            Math.min(0.065, length * 0.35),
            Math.min(0.038, length * 0.2),
          );
          arrow.name = `${STRUCTURAL_OVERLAY_NAME}RestraintDemand-${demandMarker.id}`;
          arrow.renderOrder = 38;
          arrow.traverse((object) => {
            object.userData.tertiusStructuralOverlay = true;
            object.userData.tertiusStructuralRestraint = { id: demandMarker.traceId };
            object.renderOrder = 38;
            const material = (object as THREE.Mesh | THREE.Line).material;
            const materials = Array.isArray(material) ? material : material ? [material] : [];
            for (const candidate of materials) {
              candidate.depthTest = false;
              candidate.transparent = true;
              candidate.opacity = 0.96;
            }
          });
          memberGroup.add(arrow);
        }
      }

      for (const loadArrow of structuralOverlay.loadArrows ?? []) {
        const sourceForce = new THREE.Vector3(
          loadArrow.force_kN.x,
          loadArrow.force_kN.y,
          loadArrow.force_kN.z,
        );
        const magnitude = sourceForce.length();
        if (magnitude <= Number.EPSILON) continue;
        const direction = toModelCoordinates(sourceForce).normalize();
        const origin = toModelCoordinates(new THREE.Vector3(
          loadArrow.position.x,
          loadArrow.position.y,
          loadArrow.position.z,
        ));
        const length = 0.08 + 0.18 * Math.min(1, magnitude / loadArrowPeak);
        const arrow = new THREE.ArrowHelper(
          direction,
          origin,
          length,
          0x38bdf8,
          Math.min(0.06, length * 0.35),
          Math.min(0.035, length * 0.2),
        );
        arrow.name = `${STRUCTURAL_OVERLAY_NAME}Load-${loadArrow.id}`;
        arrow.renderOrder = 33;
        arrow.traverse((object) => {
          object.userData.tertiusStructuralOverlay = true;
          object.renderOrder = 33;
          const material = (object as THREE.Mesh | THREE.Line).material;
          const materials = Array.isArray(material) ? material : material ? [material] : [];
          for (const candidate of materials) {
            candidate.depthTest = false;
            candidate.transparent = true;
            candidate.opacity = 0.95;
          }
        });
        memberGroup.add(arrow);
      }

      const stations = structuralOverlay.stations;
      if (stations.length < 2) {
        group.add(memberGroup);
        continue;
      }
      const first = stations[0]!;
      const last = stations[stations.length - 1]!;
      const axis = new THREE.Vector3(
        last.position.x - first.position.x,
        last.position.y - first.position.y,
        last.position.z - first.position.z,
      );
      if (axis.lengthSq() === 0) {
        group.add(memberGroup);
        continue;
      }
      axis.normalize();

      const values = stations.map((station) => {
        const value = overlayMode === 'displacement'
          ? station.displacement_mm
          : station.moment_kNm;
        return new THREE.Vector3(value?.x ?? 0, value?.y ?? 0, value?.z ?? 0);
      });
      const axisPoints = stations.map(({ position }) => toModelCoordinates(
        new THREE.Vector3(position.x, position.y, position.z),
      ));
      const memberStatusLine = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(axisPoints),
        new THREE.LineBasicMaterial({
          color: statusColor,
          transparent: true,
          opacity: 1,
          depthTest: false,
        }),
      );
      memberStatusLine.name = `${STRUCTURAL_OVERLAY_NAME}Member-${structuralOverlay.id}`;
      memberStatusLine.renderOrder = 32;
      memberStatusLine.userData.tertiusStructuralOverlay = true;
      memberGroup.add(memberStatusLine);

      const peakValue = Math.max(...values.map((value) => value.length()));
      if (peakValue <= Number.EPSILON) {
        group.add(memberGroup);
        continue;
      }

      // Build123D emits GLTF vertex coordinates in metres even though the CAD
      // source is authored in millimetres.
      const maxOffset = (structuralOverlay.maxOffsetMm ?? 260) / 1000;
      const diagramPoints: THREE.Vector3[] = [];
      const demandRatios: number[] = [];
      stations.forEach(({ position }, index) => {
        const sourcePoint = new THREE.Vector3(
          position.x,
          position.y,
          position.z,
        );
        const value = values[index]!;
        const offset = overlayMode === 'displacement'
          ? value.clone().multiplyScalar(maxOffset / peakValue)
          : axis.clone().cross(value).multiplyScalar(maxOffset / peakValue);
        diagramPoints.push(toModelCoordinates(sourcePoint.clone().add(offset)));
        demandRatios.push(Math.min(1, value.length() / peakValue));
      });

      const ribbonPositions: number[] = [];
      const ribbonColors: number[] = [];
      const peakColor = new THREE.Color(diagramColor);
      const lowColor = peakColor.clone().lerp(new THREE.Color(0x0f172a), 0.58);
      const pushVertex = (point: THREE.Vector3, demandRatio: number) => {
        const color = lowColor.clone().lerp(peakColor, demandRatio);
        ribbonPositions.push(point.x, point.y, point.z);
        ribbonColors.push(color.r, color.g, color.b);
      };
      for (let index = 0; index < stations.length - 1; index += 1) {
        const axisStart = axisPoints[index]!;
        const axisEnd = axisPoints[index + 1]!;
        const diagramStart = diagramPoints[index]!;
        const diagramEnd = diagramPoints[index + 1]!;
        const startDemand = demandRatios[index]!;
        const endDemand = demandRatios[index + 1]!;
        pushVertex(axisStart, startDemand);
        pushVertex(diagramStart, startDemand);
        pushVertex(axisEnd, endDemand);
        pushVertex(diagramStart, startDemand);
        pushVertex(diagramEnd, endDemand);
        pushVertex(axisEnd, endDemand);
      }
      const ribbonGeometry = new THREE.BufferGeometry();
      ribbonGeometry.setAttribute(
        'position',
        new THREE.Float32BufferAttribute(ribbonPositions, 3),
      );
      ribbonGeometry.setAttribute(
        'color',
        new THREE.Float32BufferAttribute(ribbonColors, 3),
      );
      const ribbon = new THREE.Mesh(
        ribbonGeometry,
        new THREE.MeshBasicMaterial({
          vertexColors: true,
          transparent: true,
          opacity: 0.72,
          side: THREE.DoubleSide,
          depthTest: false,
          depthWrite: false,
        }),
      );
      ribbon.name = `${STRUCTURAL_OVERLAY_NAME}Ribbon-${structuralOverlay.id}`;
      ribbon.renderOrder = 30;
      ribbon.userData.tertiusStructuralOverlay = true;
      memberGroup.add(ribbon);

      const diagramGeometry = new THREE.BufferGeometry().setFromPoints(diagramPoints);
      const diagramLine = new THREE.Line(
        diagramGeometry,
        new THREE.LineBasicMaterial({
          color: diagramColor,
          transparent: true,
          opacity: 1,
          depthTest: false,
        }),
      );
      diagramLine.name = `${STRUCTURAL_OVERLAY_NAME}Edge-${structuralOverlay.id}`;
      diagramLine.renderOrder = 31;
      diagramLine.userData.tertiusStructuralOverlay = true;
      memberGroup.add(diagramLine);

      const connectorPoints: THREE.Vector3[] = [];
      const connectorInterval = Math.max(1, Math.floor(stations.length / 8));
      axisPoints.forEach((point, index) => {
        if (
          index === 0
          || index === axisPoints.length - 1
          || index % connectorInterval === 0
        ) {
          connectorPoints.push(point, diagramPoints[index]!);
        }
      });
      const connectors = new THREE.LineSegments(
        new THREE.BufferGeometry().setFromPoints(connectorPoints),
        new THREE.LineBasicMaterial({
          color: diagramColor,
          transparent: true,
          opacity: 0.48,
          depthTest: false,
        }),
      );
      connectors.name = `${STRUCTURAL_OVERLAY_NAME}Stations-${structuralOverlay.id}`;
      connectors.renderOrder = 29;
      connectors.userData.tertiusStructuralOverlay = true;
      memberGroup.add(connectors);
      group.add(memberGroup);
    }

    model.add(group);
    return () => {
      if (group.parent) group.parent.remove(group);
      disposeObjectTree(group);
    };
  }, [sceneGraph, structuralOverlays]);

  // 4. Handle Raycasting Interactions
  useEffect(() => {
    if (!canvasRef.current || !sceneRef.current || !cameraRef.current) return;
    const canvas = canvasRef.current;
    
    const onMouseClick = (e: MouseEvent) => {
       const rect = canvas.getBoundingClientRect();
       const mouse = new THREE.Vector2(
         ((e.clientX - rect.left) / rect.width) * 2 - 1,
         -((e.clientY - rect.top) / rect.height) * 2 + 1
       );
       
       const raycaster = new THREE.Raycaster();
       raycaster.setFromCamera(mouse, cameraRef.current!);
       
       if (meshRef.current) {
          const overlayIntersection = raycaster
            .intersectObject(meshRef.current, true)
            .find((intersection) => {
              let object: THREE.Object3D | null = intersection.object;
              while (object) {
                if (object.userData.tertiusStructuralRestraint) return true;
                object = object.parent;
              }
              return false;
            });
          if (overlayIntersection) {
            let object: THREE.Object3D | null = overlayIntersection.object;
            while (object && !object.userData.tertiusStructuralRestraint) {
              object = object.parent;
            }
            const restraintId = object?.userData.tertiusStructuralRestraint?.id;
            if (typeof restraintId === 'string') {
              structuralRestraintSelectRef.current?.(restraintId);
              return;
            }
          }
          // Raycast source meshes, then ignore objects hidden in the Assembly Tree.
          meshRef.current.traverse(c => {
            if (c.userData.tertiusStructuralOverlay) return;
            if (!isViewerBatchMesh(c) && (c as THREE.Mesh).isMesh) c.visible = true;
          });
          
          const intersects = raycaster
            .intersectObject(meshRef.current, true)
            .filter(intersection => (
              !isViewerBatchMesh(intersection.object)
              && !isViewerObjectHidden(meshRef.current!, intersection.object, appearanceByPathRef.current)
            ));
          
          // Re-hide them (they'll be unhidden by the selection effect if needed)
          meshRef.current.traverse(c => {
            if (c.userData.tertiusStructuralOverlay) return;
            if (!isViewerBatchMesh(c) && (c as THREE.Mesh).isMesh) c.visible = false;
          });
          if (intersects.length > 0) {
             const node = closestSelectableSceneNode(intersects[0]!.object, meshRef.current);
             handleSelectNode(node);
          } else {
             handleSelectNode(null);
          }
       }
    };
    
    canvas.addEventListener('click', onMouseClick);
    
    return () => {
       canvas.removeEventListener('click', onMouseClick);
    };
  }, []);

  const handleSelectNode = (node: THREE.Object3D | null) => {
     setActiveCollisionId(null);
     if (!node) {
        setSelectedNodeId(null);
        localStorage.removeItem(SCENE_NODE_SELECTION_STORAGE_KEY);
        window.dispatchEvent(new Event('storage'));
        return;
     }
     
     localStorage.setItem(SCENE_NODE_SELECTION_STORAGE_KEY, createSceneNodeSelectionValue(meshRef.current, node));
     window.dispatchEvent(new Event('storage'));
     setSelectedNodeId(node.uuid);
  };

  const runCollisionAnalysis = useCallback(async () => {
    const model = meshRef.current;
    if (!model) return;
    collisionScanAbortRef.current?.abort();
    const controller = new AbortController();
    collisionScanAbortRef.current = controller;
    const minimumPenetration = Number.isFinite(collisionMinimumPenetration)
      ? Math.max(0, collisionMinimumPenetration)
      : 1;
    setCollisionPanelOpen(true);
    setCollisionAnalysis(null);
    setActiveCollisionId(null);
    setCopiedCollisionId(null);
    setCollisionScanState({ phase: 'broad', processed: 0, total: 0 });

    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    if (controller.signal.aborted) return;

    try {
      let lastBroadPhaseUpdateAt = 0;
      const candidates = await analyzePotentialCollisionsAsync(model, {
        minimumPenetrationMm: minimumPenetration,
        maxPairs: Number.MAX_SAFE_INTEGER,
        signal: controller.signal,
        onProgress: (processed, total) => {
          const now = performance.now();
          if (processed !== total && now - lastBroadPhaseUpdateAt < 100) return;
          lastBroadPhaseUpdateAt = now;
          setCollisionScanState({ phase: 'broad', processed, total });
        },
      });
      setCollisionScanState({ phase: 'prepare', processed: 0, total: 0 });
      let lastPreparationUpdateAt = 0;
      let lastVerificationUpdateAt = 0;
      const analysis = await verifyPotentialCollisions(candidates, {
        maxPairs: Number.MAX_SAFE_INTEGER,
        minimumPenetrationMm: minimumPenetration,
        signal: controller.signal,
        onPreparationProgress: (processed, total) => {
          const now = performance.now();
          if (processed !== total && now - lastPreparationUpdateAt < 100) return;
          lastPreparationUpdateAt = now;
          setCollisionScanState({ phase: 'prepare', processed, total });
        },
        onProgress: (processed, total) => {
          const now = performance.now();
          if (processed !== total && now - lastVerificationUpdateAt < 100) return;
          lastVerificationUpdateAt = now;
          setCollisionScanState({ phase: 'narrow', processed, total });
        },
        onPartialResult: (partialAnalysis) => {
          setCollisionAnalysis(partialAnalysis);
          setActiveCollisionId(current => current || partialAnalysis.pairs[0]?.id || null);
        },
      });
      if (controller.signal.aborted) return;
      setCollisionAnalysis(analysis);
      setCollisionScanState({
        phase: 'idle',
        processed: analysis.candidatePairCount,
        total: analysis.candidatePairCount,
      });
      setActiveCollisionId(analysis.pairs[0]?.id || null);
      if (analysis.pairs[0]) {
        const bounds = analysis.pairs[0].a.bounds.clone().union(analysis.pairs[0].b.bounds);
        frameCameraOnBox(bounds, 1.25);
      }
    } catch (error) {
      if (controller.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) return;
      setCollisionScanState({
        phase: 'error',
        processed: 0,
        total: 0,
        message: error instanceof Error ? error.message : 'Mesh collision analysis failed',
      });
    } finally {
      if (collisionScanAbortRef.current === controller) collisionScanAbortRef.current = null;
    }
  }, [collisionMinimumPenetration, frameCameraOnBox]);

  const cancelCollisionAnalysis = useCallback(() => {
    const controller = collisionScanAbortRef.current;
    if (!controller) return;
    controller.abort();
    collisionScanAbortRef.current = null;
    setCollisionScanState(previous => ({
      phase: 'cancelled',
      processed: previous.processed,
      total: previous.total,
      message: 'Collision scan cancelled.',
    }));
  }, []);

  const inspectCollision = useCallback((pair: PotentialCollision) => {
    setActiveCollisionId(pair.id);
    setAutoRotate(false);
    frameCameraOnBox(pair.a.bounds.clone().union(pair.b.bounds), 1.25);
  }, [frameCameraOnBox]);

  const copyCollisionDetails = useCallback(async (pair: PotentialCollision) => {
    try {
      await navigator.clipboard.writeText(collisionClipboardText(pair));
      setCopiedCollisionId(pair.id);
    } catch (error) {
      console.warn('Could not copy collision details', error);
    }
  }, []);

  const activeCollision = collisionAnalysis?.pairs.find(pair => pair.id === activeCollisionId) || null;

  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !activeCollision?.contactPoint) return;
    const pairBounds = activeCollision.a.bounds.clone().union(activeCollision.b.bounds);
    const radius = THREE.MathUtils.clamp(
      pairBounds.getBoundingSphere(new THREE.Sphere()).radius * 0.018,
      0.006,
      0.035,
    );
    const marker = new THREE.Mesh(
      new THREE.OctahedronGeometry(radius, 1),
      new THREE.MeshBasicMaterial({
        color: 0xf472b6,
        transparent: true,
        opacity: 0.98,
        depthTest: false,
        depthWrite: false,
      }),
    );
    marker.position.copy(activeCollision.contactPoint);
    marker.name = 'TertiusCollisionContact';
    marker.renderOrder = 50;
    scene.add(marker);

    return () => {
      scene.remove(marker);
      marker.geometry.dispose();
      disposeMaterial(marker.material);
    };
  }, [activeCollision]);

  useEffect(() => {
    const handleStorage = () => {
      const selectedValue = localStorage.getItem(SCENE_NODE_SELECTION_STORAGE_KEY);
      setAppearanceByPath(readSceneNodeAppearanceMap(localStorage.getItem(SCENE_NODE_APPEARANCE_STORAGE_KEY)));
      if (!selectedValue) {
         setSelectedNodeId(null);
         return;
      }
      
      if (meshRef.current) {
         const node = resolveSceneNodeSelection(meshRef.current, selectedValue);
         if (node) {
            setSelectedNodeId(node.uuid);
         }
      }
    };
    
    handleStorage();
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, [sceneGraph]);

  useEffect(() => {
    const frameTargetValue = (value: string | null) => {
      if (!meshRef.current || !value) return;
      const node = resolveSceneNodeSelection(meshRef.current, value);
      if (!node) return;
      frameCameraOnBox(getRenderableObjectBounds(node), 1.08);
    };

    const handleTarget = (event: Event) => {
      const detail = (event as CustomEvent<{ value?: unknown }>).detail;
      const value = typeof detail?.value === 'string'
        ? detail.value
        : localStorage.getItem(SCENE_NODE_TARGET_STORAGE_KEY);
      frameTargetValue(value);
    };

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== SCENE_NODE_TARGET_STORAGE_KEY) return;
      frameTargetValue(event.newValue);
    };

    window.addEventListener(SCENE_NODE_TARGET_EVENT, handleTarget);
    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener(SCENE_NODE_TARGET_EVENT, handleTarget);
      window.removeEventListener('storage', handleStorage);
    };
  }, [frameCameraOnBox, sceneGraph]);


  // 5. Apply visibility and highlights
  useEffect(() => {
     if (!meshRef.current) return;
     const model = meshRef.current;
     const batchedMesh = model.userData.batchedMesh;
     const appearanceBatchMesh = model.userData.appearanceBatchMesh as THREE.Mesh | undefined;
      const instancedMeshes = model.userData.instancedMeshes as THREE.InstancedMesh[] | undefined;
      const sharedMaterial = model.userData.sharedMat as THREE.MeshStandardMaterial | undefined;
      const highlightMaterial = model.userData.highlightMat as THREE.Material | undefined;
      const hasAppearanceOverrides = Object.values(appearanceByPath).some(appearance => appearance.hidden || appearance.transparent);
      const appearanceBatchKey = JSON.stringify(appearanceByPath);
      const externallySelectedIds = externalSelectedNodeIds ? new Set(externalSelectedNodeIds.filter(Boolean)) : null;
      const normalizedExternalIds = new Set([...(externallySelectedIds || new Set<string>())].map(normalizeExternalSelectionId).filter(Boolean));
      const externalSelection = externallySelectedIds?.size ? resolveExternalSelectionMeshes(model, externallySelectedIds) : null;
      const hasRenderableExternalSelection = Boolean(externalSelection?.hasSelection);
      const hasActiveCollision = Boolean(activeCollision) && !hasRenderableExternalSelection;
      const collisionNodeAId = activeCollision?.a.node.uuid;
      const collisionNodeBId = activeCollision?.b.node.uuid;
      const selectedNodeIds = externallySelectedIds || (selectedNodeId ? new Set([selectedNodeId]) : new Set<string>());
      const isNodeSelected = (node: THREE.Object3D) => (
        externallySelectedIds
          ? matchesExternalSelection(node, externallySelectedIds, normalizedExternalIds)
          : selectedNodeIds.has(node.uuid) || Boolean(node.name && selectedNodeIds.has(node.name))
      );

     const removeAppearanceBatch = () => {
        const currentBatch = model.userData.appearanceBatchMesh as THREE.Mesh | undefined;
        if (!currentBatch) return;
        model.remove(currentBatch);
        disposeMesh(currentBatch);
        model.userData.appearanceBatchMesh = undefined;
        model.userData.appearanceBatchKey = '';
     };

     if (hasAppearanceOverrides && !hasActiveCollision && model.userData.appearanceBatchKey !== appearanceBatchKey) {
        removeAppearanceBatch();

        const opaqueMeshes: THREE.Mesh[] = [];
        model.traverse((child) => {
           if (child.userData.tertiusStructuralOverlay) return;
           if (isViewerBatchMesh(child) || !(child as THREE.Mesh).isMesh) return;

           let isHidden = false;
           let isTransparent = false;
           let p: THREE.Object3D | null = child;
           const material = child.userData.viewerSourceMaterial as THREE.Material | THREE.Material[] | undefined;
           const hasModelTransparency = hasSourceMaterialTransparency(material);

           while (p && p !== model) {
              const appearance = appearanceByPath[getSceneNodePathKey(model, p)];
              if (appearance?.hidden) isHidden = true;
              if (appearance?.transparent) isTransparent = true;
              p = p.parent;
           }

           if (!isHidden && !isTransparent && !hasModelTransparency) {
              let geometry = child.userData.viewerBatchGeometry as THREE.BufferGeometry | undefined;
              if (!geometry) {
                const matrix = child.userData.viewerBatchMatrix as THREE.Matrix4 | undefined;
                if (matrix) {
                  geometry = (child as THREE.Mesh).geometry.clone();
                  applyViewerGeometryTransform(geometry, matrix);
                  child.userData.viewerBatchGeometry = geometry;
                }
              }
              if (geometry && material) opaqueMeshes.push(new THREE.Mesh(geometry, material));
           }
        });

        const appearanceBatch = buildViewerBatch(opaqueMeshes);
        if (appearanceBatch) {
           appearanceBatch.mesh.name = "TertiusAppearanceBatchMesh";
           appearanceBatch.mesh.castShadow = renderQuality === 'high';
           appearanceBatch.mesh.receiveShadow = renderQuality === 'high';
           model.add(appearanceBatch.mesh);
           model.userData.appearanceBatchMesh = appearanceBatch.mesh;
           model.userData.appearanceBatchKey = appearanceBatchKey;
        }
     } else if (!hasAppearanceOverrides || hasActiveCollision) {
        removeAppearanceBatch();
     }
     
     // Reset batched mesh
     if (batchedMesh) batchedMesh.visible = !hasAppearanceOverrides && !hasRenderableExternalSelection && !hasActiveCollision;
     instancedMeshes?.forEach((mesh) => {
       mesh.visible = !hasAppearanceOverrides && !hasRenderableExternalSelection && !hasActiveCollision;
     });
     if (appearanceBatchMesh) appearanceBatchMesh.visible = hasAppearanceOverrides && !hasRenderableExternalSelection && !hasActiveCollision;
     
     // Evaluate visibility for individual meshes based on selection or isolation
     model.traverse((child) => {
        if (child.userData.tertiusStructuralOverlay) return;
        if (isViewerBatchMesh(child)) return;
        
        if ((child as THREE.Mesh).isMesh) {
           const mesh = child as THREE.Mesh;
           
           let isSelected = false;
           let isHidden = false;
           let isTransparent = false;
           let collisionRole: 'a' | 'b' | null = null;
           const hasModelTransparency = hasSourceMaterialTransparency(mesh.userData.viewerSourceMaterial as THREE.Material | THREE.Material[] | undefined);
            let p: THREE.Object3D | null = child;

            while (p && p !== model) {
               if (isNodeSelected(p)) isSelected = true;
               if (p.uuid === collisionNodeAId) collisionRole = 'a';
               if (p.uuid === collisionNodeBId) collisionRole = 'b';
               const appearance = appearanceByPath[getSceneNodePathKey(model, p)];
               if (appearance?.hidden) isHidden = true;
              if (appearance?.transparent) isTransparent = true;
              p = p.parent;
           }
           
           if (hasActiveCollision) {
              mesh.visible = collisionRole !== null;
           } else if (hasRenderableExternalSelection) {
              mesh.visible = Boolean(externalSelection?.meshes.has(mesh)) && !isHidden;
           } else if (hasAppearanceOverrides) {
              mesh.visible = !isHidden && (isTransparent || isSelected || hasModelTransparency);
           } else {
              // In normal mode, only the selected parts are visible (as an overlay on the batched mesh)
              mesh.visible = isSelected || hasModelTransparency;
           }

           if (mesh.visible) {
              let viewerMaterials = mesh.userData.viewerMaterials as ViewerMeshMaterials | undefined;
              if (!viewerMaterials && sharedMaterial) {
                viewerMaterials = createViewerMeshMaterials(
                  mesh.userData.viewerSourceMaterial as THREE.Material | THREE.Material[] | undefined,
                  sharedMaterial,
                );
                mesh.userData.viewerMaterials = viewerMaterials;
              }
              const shouldHighlightSelection = isSelected && !hasRenderableExternalSelection;
              if (collisionRole === 'a' && viewerMaterials) {
                 mesh.material = viewerMaterials.collisionA;
              } else if (collisionRole === 'b' && viewerMaterials) {
                 mesh.material = viewerMaterials.collisionB;
              } else if (isTransparent && shouldHighlightSelection && viewerMaterials) {
                 mesh.material = viewerMaterials.transparentHighlight;
              } else if (shouldHighlightSelection && viewerMaterials) {
                 mesh.material = viewerMaterials.highlight;
              } else if (isTransparent && viewerMaterials) {
                 mesh.material = viewerMaterials.transparent;
              } else if (viewerMaterials) {
                 mesh.material = viewerMaterials.base;
              } else if (shouldHighlightSelection && highlightMaterial) {
                 mesh.material = highlightMaterial;
              } else if (sharedMaterial) {
                 mesh.material = sharedMaterial;
              }
           }
        }
     });

   }, [
     selectedNodeId,
     sceneGraph,
     appearanceByPath,
     renderQuality,
     externalSelectedNodeIds,
     externalSelectionKey,
     activeCollision,
   ]);

  useEffect(() => {
    if (!onExternalSelectionPreviewChange) return;
    if (!externalSelectionKey || !sceneGraph) {
      onExternalSelectionPreviewChange(null);
      return;
    }
    onExternalSelectionPreviewChange(null);
    const timer = window.setTimeout(() => {
      onExternalSelectionPreviewChange(captureExternalSelectionPreview());
    }, 180);
    return () => window.clearTimeout(timer);
  }, [captureExternalSelectionPreview, externalSelectionKey, onExternalSelectionPreviewChange, sceneGraph]);

  return (
    <div className="flex-1 relative bg-slate-900 flex overflow-hidden">
      <ViewerControls
        projectName={projectName}
        structuralOverlays={structuralOverlays}
        renderQuality={renderQuality}
        showGrid={showGrid}
        autoRotate={autoRotate}
        collisionPanelOpen={collisionPanelOpen}
        collisionScanRunning={collisionScanRunning}
        collisionCount={collisionAnalysis?.confirmedPairCount}
        collisionScanDisabled={!sceneGraph || collisionScanRunning}
        loadErrorText={loadErrorText}
        isModelLoading={isModelLoading}
        statusText={statusText}
        instancingStats={viewerInstancingStats}
        onFit={() => frameModelRoot(1.5)}
        onRunCollisionAnalysis={runCollisionAnalysis}
        onToggleRenderQuality={() => setRenderQuality(renderQuality === 'high' ? 'low' : 'high')}
        onToggleGrid={() => setShowGrid(!showGrid)}
        onToggleAutoRotate={() => setAutoRotate(!autoRotate)}
      />

      {collisionPanelOpen && (
        <aside className="absolute right-4 top-4 bottom-4 z-20 flex w-[min(24rem,calc(100%-2rem))] flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-950/95 text-slate-200 shadow-2xl backdrop-blur">
          <div className="border-b border-slate-800 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-bold text-white">Verified mesh collisions</h2>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  AABB shortlist followed by BVH triangle-to-triangle confirmation.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  cancelCollisionAnalysis();
                  setCollisionPanelOpen(false);
                  setActiveCollisionId(null);
                }}
                className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-slate-500 hover:text-white"
                aria-label="Close overlap inspector"
              >
                Close
              </button>
            </div>

            <div className="mt-3 flex items-end gap-2">
              <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-slate-400">
                Ignore candidates whose box overlap is below
                <span className="flex items-center gap-2">
                  <input
                    type="number"
                    min="0"
                    step="0.5"
                    value={collisionMinimumPenetration}
                    onChange={event => setCollisionMinimumPenetration(Number(event.target.value))}
                    className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-white outline-none focus:border-sky-500"
                  />
                  <span>mm</span>
                </span>
              </label>
              <button
                type="button"
                onClick={collisionScanRunning ? cancelCollisionAnalysis : runCollisionAnalysis}
                className={`rounded border px-3 py-1.5 text-xs font-bold text-white ${collisionScanRunning ? 'border-rose-600 bg-rose-700 hover:bg-rose-600' : 'border-sky-600 bg-sky-700 hover:bg-sky-600'}`}
              >
                {collisionScanRunning ? 'Cancel scan' : 'Scan again'}
              </button>
            </div>

            {collisionScanState.phase === 'broad' && (
              <div className="mt-3 text-xs text-sky-300" aria-live="polite">
                Shortlisting component boxes{collisionScanState.total > 0 ? `: ${collisionScanState.processed} / ${collisionScanState.total}` : '…'}
              </div>
            )}
            {collisionScanState.phase === 'prepare' && (
              <div className="mt-3 text-xs text-sky-300" aria-live="polite">
                Preparing rendered meshes: {collisionScanState.processed} / {collisionScanState.total}
              </div>
            )}
            {collisionScanState.phase === 'narrow' && (
              <div className="mt-3 text-xs text-sky-300" aria-live="polite">
                Verifying rendered triangles: {collisionScanState.processed} / {collisionScanState.total} candidates
              </div>
            )}
            {collisionScanState.phase === 'cancelled' && (
              <div className="mt-3 rounded border border-slate-700 bg-slate-900/60 p-2 text-xs text-slate-300" role="status">
                {collisionScanState.message}
              </div>
            )}
            {collisionScanState.phase === 'error' && (
              <div className="mt-3 rounded border border-red-900/70 bg-red-950/40 p-2 text-xs text-red-300" role="alert">
                Collision scan failed: {collisionScanState.message}
              </div>
            )}
            {collisionAnalysis && (
              <>
                <div className="mt-3 text-xs text-slate-300" aria-live="polite">
                  {collisionAnalysis.confirmedPairCount} confirmed{collisionScanRunning ? ' so far' : ''} from {collisionAnalysis.candidatePairCount} box candidate{collisionAnalysis.candidatePairCount === 1 ? '' : 's'} across {collisionAnalysis.componentCount} components
                </div>
                <label className="mt-3 block text-xs text-slate-400">
                  Find a component or design.py source
                  <input
                    type="search"
                    value={collisionSearch}
                    onChange={(event) => {
                      setCollisionSearch(event.target.value);
                      setCollisionVisibleLimit(50);
                    }}
                    placeholder="e.g. header, C100, roof sheet"
                    className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-white outline-none placeholder:text-slate-600 focus:border-sky-500"
                  />
                </label>
                <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                  <span>{filteredCollisionPairs.length} matching result{filteredCollisionPairs.length === 1 ? '' : 's'}</span>
                  <span className="font-mono">{COLLISION_ENGINE_CHECKPOINT}</span>
                </div>
              </>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-2" data-testid="collision-result-list">
            {!collisionScanRunning && collisionAnalysis?.pairs.length === 0 && (
              <div className="m-2 rounded-lg border border-emerald-900/70 bg-emerald-950/40 p-3 text-sm text-emerald-300">
                No triangle-level intersections were found among {collisionAnalysis.candidatePairCount} box candidate{collisionAnalysis.candidatePairCount === 1 ? '' : 's'}.
              </div>
            )}
            {visibleCollisionPairs.map((pair, index) => (
              <div
                key={pair.id}
                className={`mb-2 rounded-lg border p-3 transition-colors ${activeCollisionId === pair.id ? 'border-rose-500 bg-rose-950/30' : 'border-slate-800 bg-slate-900/70 hover:border-slate-600'}`}
              >
                <button
                  type="button"
                  onClick={() => inspectCollision(pair)}
                  className="w-full text-left"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs font-bold text-slate-400">Pair {index + 1}</span>
                    <span className="rounded border border-fuchsia-800/70 bg-fuchsia-950/40 px-2 py-0.5 text-[10px] font-semibold text-fuchsia-300">
                      Mesh intersection confirmed
                    </span>
                  </div>
                  <div className="mt-2 flex items-start gap-2">
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-red-500" />
                    <div className="min-w-0">
                      <div className="break-words text-sm font-semibold text-red-200">{pair.a.label}</div>
                      <div className="break-words font-mono text-[11px] leading-4 text-slate-400">{collisionSourceLabel(pair.a.sourceReferences)}</div>
                    </div>
                  </div>
                  <div className="mt-2 flex items-start gap-2">
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-amber-500" />
                    <div className="min-w-0">
                      <div className="break-words text-sm font-semibold text-amber-200">{pair.b.label}</div>
                      <div className="break-words font-mono text-[11px] leading-4 text-slate-400">{collisionSourceLabel(pair.b.sourceReferences)}</div>
                    </div>
                  </div>
                  {pair.contactPoint && (
                    <div className="mt-2 font-mono text-[10px] text-fuchsia-300">
                      Contact marker: {[pair.contactPoint.x, pair.contactPoint.y, pair.contactPoint.z]
                        .map(value => (value * 1000).toFixed(1))
                        .join(', ')} mm
                    </div>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => copyCollisionDetails(pair)}
                  className="mt-3 rounded border border-slate-700 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:border-sky-600 hover:text-sky-300"
                >
                  {copiedCollisionId === pair.id ? 'Copied' : 'Copy design.py fix context'}
                </button>
              </div>
            ))}
            {visibleCollisionPairs.length < filteredCollisionPairs.length && (
              <button
                type="button"
                onClick={() => setCollisionVisibleLimit(limit => limit + 50)}
                className="mb-2 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-300 hover:border-sky-600 hover:text-sky-300"
              >
                Show 50 more ({filteredCollisionPairs.length - visibleCollisionPairs.length} remaining)
              </button>
            )}
          </div>

          <div className="border-t border-slate-800 p-3 text-[11px] leading-4 text-slate-500">
            Mesh-level verification removes empty-space and rotated-box false positives. Exact CAD/B-Rep confirmation is still required for certification and fully contained solids.
          </div>
        </aside>
      )}
      
      {/* 3D Canvas */}
      <div className="flex-1 relative" ref={containerRef}>
        <canvas ref={canvasRef} className="w-full h-full block outline-none" />
      </div>
    </div>
  );
};
