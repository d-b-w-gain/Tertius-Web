import React, { useState, useEffect, useRef, useCallback } from 'react';
import { SpanStatusCode } from '@opentelemetry/api';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
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

interface ViewerProps {
  serverUrl: string;
  isActive?: boolean;
  statusTextOverride?: string;
  externalSelectedNodeIds?: string[];
  onExternalSelectionPreviewChange?: (preview: ComponentPreviewImage | null) => void;
}

interface ModelViewerCanvasProps {
  modelUrl: string;
  getAccessToken: () => Promise<string>;
  statusText?: string;
  projectName?: string;
  isActive?: boolean;
  externalSelectedNodeIds?: string[];
  onExternalSelectionPreviewChange?: (preview: ComponentPreviewImage | null) => void;
}

export const DEFAULT_MODEL_COLOR = 0x8b9bb4;
const COMPONENT_PREVIEW_SIZE = 512;

const normalizeExternalSelectionId = (value: string) => value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '');

type GltfNodeJson = {
  children?: unknown;
};

type GltfSceneJson = {
  nodes?: unknown;
};

type GltfParserJson = {
  nodes?: unknown;
  scenes?: unknown;
  scene?: unknown;
};

function annotateGltfNodeIds(root: THREE.Object3D, gltfJson: GltfParserJson | undefined): void {
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

  const annotateNode = (object: THREE.Object3D | undefined, nodeId: number) => {
    if (!object || !nodes[nodeId]) return;
    object.userData.tertiusGltfNodeId = String(nodeId);
    const childNodeIds = Array.isArray(nodes[nodeId].children)
      ? nodes[nodeId].children.filter((value): value is number => Number.isInteger(value))
      : [];
    childNodeIds.forEach((childNodeId, childIndex) => annotateNode(object.children[childIndex], childNodeId));
  };

  sceneNodeIds.forEach((nodeId, childIndex) => annotateNode(root.children[childIndex], nodeId));
}

const matchesExternalSelection = (
  object: THREE.Object3D,
  selectedIds: Set<string>,
  normalizedSelectedIds: Set<string>,
) => (
  selectedIds.has(object.uuid)
  || Boolean(object.userData?.tertiusGltfNodeId && selectedIds.has(String(object.userData.tertiusGltfNodeId)))
  || Boolean(object.name && selectedIds.has(object.name))
  || Boolean(object.name && normalizedSelectedIds.has(normalizeExternalSelectionId(object.name)))
);

const getRenderableObjectBounds = (object: THREE.Object3D) => {
  const bounds = new THREE.Box3();

  object.traverse((child) => {
    if (isViewerBatchMesh(child) || !(child as THREE.Mesh).isMesh) return;
    const meshBox = new THREE.Box3().setFromObject(child);
    if (!meshBox.isEmpty()) bounds.union(meshBox);
  });

  if (bounds.isEmpty()) {
    const objectBox = new THREE.Box3().setFromObject(object);
    if (!objectBox.isEmpty()) bounds.union(objectBox);
  }

  return bounds;
};

const resolveExternalSelectionMeshes = (model: THREE.Object3D, selectedIds: Set<string>) => {
  const normalizedSelectedIds = new Set([...selectedIds].map(normalizeExternalSelectionId).filter(Boolean));
  const bounds = new THREE.Box3();
  const meshes = new Set<THREE.Mesh>();
  let focusObject: THREE.Object3D | null = null;

  model.traverse((child) => {
    if (isViewerBatchMesh(child) || !(child as THREE.Mesh).isMesh) return;
    const mesh = child as THREE.Mesh;
    let current: THREE.Object3D | null = mesh;
    while (current && current !== model) {
      if (matchesExternalSelection(current, selectedIds, normalizedSelectedIds)) {
        focusObject = focusObject || current;
        const meshBox = new THREE.Box3().setFromObject(mesh);
        if (!meshBox.isEmpty()) {
          bounds.union(meshBox);
          meshes.add(mesh);
        }
        return;
      }
      current = current.parent;
    }
  });

  const focusBounds = focusObject ? getRenderableObjectBounds(focusObject) : new THREE.Box3();

  return {
    bounds,
    focusBounds: focusBounds.isEmpty() ? bounds : focusBounds,
    focusObject,
    meshes,
    hasSelection: meshes.size > 0 && !bounds.isEmpty(),
  };
};

type ViewerBatchOptions = {
  createMesh?: (geometry: THREE.BufferGeometry, material: THREE.Material) => THREE.Mesh;
  useAuthoredColors?: boolean;
};

type ViewerBatch = {
  mesh: THREE.Mesh;
  usesAuthoredColors: boolean;
};

type ViewerMeshMaterials = {
  base: THREE.Material | THREE.Material[];
  highlight: THREE.Material | THREE.Material[];
  transparent: THREE.Material | THREE.Material[];
  transparentHighlight: THREE.Material | THREE.Material[];
};

function materialList(material: THREE.Material | THREE.Material[]): THREE.Material[] {
  return Array.isArray(material) ? material : [material];
}

export function hasAuthoredMaterialColor(material: THREE.Material | THREE.Material[] | null | undefined): boolean {
  if (!material) return false;
  return materialList(material).some((mat) => mat.userData?.tertiusAuthoredColor === true && 'color' in mat);
}

function hasSourceMaterialTransparency(material: THREE.Material | THREE.Material[] | null | undefined): boolean {
  if (!material) return false;
  return materialList(material).some((mat) => mat.transparent === true && 'opacity' in mat && mat.opacity < 1);
}

function colorFromMaterial(material: THREE.Material | THREE.Material[] | null | undefined): THREE.Color | null {
  if (!material) return null;
  const authored = materialList(material).find((mat) => mat.userData?.tertiusAuthoredColor === true && 'color' in mat);
  const color = authored && 'color' in authored ? (authored as THREE.MeshStandardMaterial).color : null;
  return color ? color.clone() : null;
}

function geometryWithVertexColor(geometry: THREE.BufferGeometry, color: THREE.Color): THREE.BufferGeometry {
  if (geometry.getAttribute('color')) return geometry;
  const position = geometry.getAttribute('position');
  if (!position) return geometry;
  const colors = new Float32Array(position.count * 3);
  for (let i = 0; i < position.count; i += 1) {
    colors[i * 3] = color.r;
    colors[i * 3 + 1] = color.g;
    colors[i * 3 + 2] = color.b;
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return geometry;
}

function cloneViewerMaterial(
  material: THREE.Material,
  fallback: THREE.MeshStandardMaterial,
  configure?: (clone: THREE.Material) => void,
): THREE.Material {
  const clone = material.clone();
  clone.side = THREE.FrontSide;
  if ('metalness' in clone && 'metalness' in fallback) {
    (clone as THREE.MeshStandardMaterial).metalness = fallback.metalness;
  }
  if ('roughness' in clone && 'roughness' in fallback) {
    (clone as THREE.MeshStandardMaterial).roughness = fallback.roughness;
  }
  configure?.(clone);
  return clone;
}

function createViewerMaterialVariant(
  material: THREE.Material | THREE.Material[],
  fallback: THREE.MeshStandardMaterial,
  configure?: (clone: THREE.Material) => void,
): THREE.Material | THREE.Material[] {
  return Array.isArray(material)
    ? material.map(mat => cloneViewerMaterial(mat, fallback, configure))
    : cloneViewerMaterial(material, fallback, configure);
}

export function createViewerMeshMaterials(
  sourceMaterial: THREE.Material | THREE.Material[] | null | undefined,
  fallbackMaterial: THREE.MeshStandardMaterial,
): ViewerMeshMaterials {
  const baseSource = sourceMaterial ?? fallbackMaterial;
  const base = createViewerMaterialVariant(baseSource, fallbackMaterial);
  const highlight = createViewerMaterialVariant(baseSource, fallbackMaterial, (mat) => {
    if ('emissive' in mat) {
      (mat as THREE.MeshStandardMaterial).emissive.setHex(0x3b82f6);
      (mat as THREE.MeshStandardMaterial).emissiveIntensity = 0.5;
    }
    mat.polygonOffset = true;
    mat.polygonOffsetFactor = -1;
    mat.polygonOffsetUnits = -1;
  });
  const transparent = createViewerMaterialVariant(baseSource, fallbackMaterial, (mat) => {
    mat.transparent = true;
    mat.opacity = 0.28;
    mat.depthWrite = false;
  });
  const transparentHighlight = createViewerMaterialVariant(baseSource, fallbackMaterial, (mat) => {
    mat.transparent = true;
    mat.opacity = 0.45;
    mat.depthWrite = false;
    if ('emissive' in mat) {
      (mat as THREE.MeshStandardMaterial).emissive.setHex(0x3b82f6);
      (mat as THREE.MeshStandardMaterial).emissiveIntensity = 0.5;
    }
    mat.polygonOffset = true;
    mat.polygonOffsetFactor = -1;
    mat.polygonOffsetUnits = -1;
  });

  return { base, highlight, transparent, transparentHighlight };
}

function disposeMaterial(material: THREE.Material | THREE.Material[] | null | undefined): void {
  if (!material) return;
  if (Array.isArray(material)) material.forEach(mat => mat.dispose());
  else material.dispose();
}

function disposeViewerMeshMaterials(materials: ViewerMeshMaterials | undefined): void {
  if (!materials) return;
  disposeMaterial(materials.base);
  disposeMaterial(materials.highlight);
  disposeMaterial(materials.transparent);
  disposeMaterial(materials.transparentHighlight);
}

function disposeMesh(mesh: THREE.Mesh): void {
  mesh.geometry.dispose();
  disposeMaterial(mesh.material);
}

function disposeObjectTree(object: THREE.Object3D): void {
  object.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      const mesh = child as THREE.Mesh;
      disposeMesh(mesh);
      disposeMaterial(mesh.userData.viewerSourceMaterial as THREE.Material | THREE.Material[] | undefined);
      disposeViewerMeshMaterials(mesh.userData.viewerMaterials as ViewerMeshMaterials | undefined);
      (mesh.userData.viewerBatchGeometry as THREE.BufferGeometry | undefined)?.dispose();
    }
  });
}

function closestSelectableSceneNode(object: THREE.Object3D, root: THREE.Object3D): THREE.Object3D {
  let current: THREE.Object3D | null = object;
  let fallback: THREE.Object3D = object;

  while (current && current !== root) {
    const isMesh = (current as THREE.Mesh).isMesh;
    const isAssemblyNode = current.type === 'Group' || current.type === 'Object3D';
    if (current.name && current.name !== 'TertiusBatchedMesh' && isAssemblyNode) return current;
    if (current.name && current.name !== 'TertiusBatchedMesh') fallback = current;
    if ((isMesh || isAssemblyNode) && !fallback.name) fallback = current;
    current = current.parent;
  }

  return fallback;
}

function isViewerBatchMesh(object: THREE.Object3D): boolean {
  return object.name === "TertiusBatchedMesh" || object.name === "TertiusAppearanceBatchMesh";
}

export function buildViewerBatch(meshes: THREE.Mesh[], options: ViewerBatchOptions = {}): ViewerBatch | null {
  if (meshes.length === 0) return null;

  const usesAuthoredColors = options.useAuthoredColors ?? meshes.some((mesh) => hasAuthoredMaterialColor(mesh.material));
  const defaultColor = new THREE.Color(DEFAULT_MODEL_COLOR);
  const geometries = meshes.map((mesh) => {
    const geometry = mesh.geometry.clone();
    if (usesAuthoredColors) {
      geometryWithVertexColor(geometry, colorFromMaterial(mesh.material) ?? defaultColor);
    }
    return geometry;
  });

  const mergedGeometry = BufferGeometryUtils.mergeGeometries(geometries, false);
  geometries.forEach((geometry) => geometry.dispose());
  if (!mergedGeometry) return null;

  const material = usesAuthoredColors
    ? new THREE.MeshStandardMaterial({
        color: 0xffffff,
        vertexColors: true,
        metalness: 0.6,
        roughness: 0.4,
        side: THREE.FrontSide,
      })
    : new THREE.MeshStandardMaterial({
        color: DEFAULT_MODEL_COLOR,
        metalness: 0.6,
        roughness: 0.4,
        side: THREE.FrontSide,
      });

  return {
    mesh: (options.createMesh ?? ((geometry, meshMaterial) => new THREE.Mesh(geometry, meshMaterial)))(mergedGeometry, material),
    usesAuthoredColors,
  };
}

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
  onExternalSelectionPreviewChange,
}) => {
  const [showGrid, setShowGrid] = useState<boolean>(true);
  const [autoRotate, setAutoRotate] = useState<boolean>(true);
  const [renderQuality, setRenderQuality] = useState<'high' | 'low'>('high');
  const [loadErrorText, setLoadErrorText] = useState<string | null>(null);
  const [isModelLoading, setIsModelLoading] = useState<boolean>(false);
  
  const [sceneGraph, setSceneGraph] = useState<THREE.Object3D | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [appearanceByPath, setAppearanceByPath] = useState<SceneNodeAppearanceMap>(() => (
    readSceneNodeAppearanceMap(localStorage.getItem(SCENE_NODE_APPEARANCE_STORAGE_KEY))
  ));
  
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const autoRotateRef = useRef<boolean>(true);
  const isActiveRef = useRef<boolean>(isActive);
  
  // THREE.js refs
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const meshRef = useRef<THREE.Object3D | null>(null);
  const animIdRef = useRef<number>(0);
  const modelLoadRequestRef = useRef<number>(0);
  const loadedModelUrlRef = useRef<string>('');
  const previousExternalSelectionKeyRef = useRef<string>('');

  const clearCurrentModel = useCallback(() => {
    const scene = sceneRef.current;
    const current = meshRef.current;
    if (!scene || !current) return;
    disposeObjectTree(current);
    scene.remove(current);
    meshRef.current = null;
    setSceneGraph(null);
    setSelectedNodeId(null);
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
    renderer.toneMappingExposure = 1.2;
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
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
    ambientLight.name = 'Ambient';
    scene.add(ambientLight);
    
    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.6);
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

    const animate = () => {
      animIdRef.current = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
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
       controlsRef.current.autoRotate = autoRotate;
    }
  }, [autoRotate]);

  useEffect(() => {
    if (!rendererRef.current || !sceneRef.current) return;
    const isHigh = renderQuality === 'high';
    
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
            ((node as THREE.Mesh).material as THREE.Material).needsUpdate = true;
         }
      }
    });
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
      render_quality: renderQuality,
    });
    const loader = new GLTFLoader();

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
    
    apiFetch(modelUrl, getAccessToken)
      .then(res => {
        if (!res.ok) {
          throw new Error(`Model artifact unavailable (${res.status || 'HTTP error'})`);
        }
        return res.arrayBuffer();
      })
      .then(buffer => {
        if (!isCurrentRequest()) return;
        loader.parse(buffer, '', (gltf) => {
          if (!isCurrentRequest()) return;
      
          const model = gltf.scene;
          annotateGltfNodeIds(model, (gltf.parser as unknown as { json?: GltfParserJson } | undefined)?.json);
      
      // Compute bounding box and center
      const box = new THREE.Box3().setFromObject(model);
      const center = new THREE.Vector3();
      box.getCenter(center);
      model.position.sub(center);
      
      // Fix orientation (GLTF is Y-up, our grid was Z-up)
      model.rotation.x = Math.PI / 2;
      
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
        metalness: 0.6,
        roughness: 0.4,
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
      
      const isHigh = renderQuality === 'high';
      
      model.updateMatrixWorld(true);
      const inverseModelMatrix = model.matrixWorld.clone().invert();
      const sourceMeshes: THREE.Mesh[] = [];
      
      model.traverse((child) => {
        if ((child as THREE.Mesh).isMesh) {
           const mesh = child as THREE.Mesh;
           const geom = mesh.geometry.clone();
           const relativeMatrix = new THREE.Matrix4().multiplyMatrices(inverseModelMatrix, mesh.matrixWorld);
           geom.applyMatrix4(relativeMatrix);
           mesh.userData.viewerSourceMaterial = mesh.material;
           mesh.userData.viewerBatchGeometry = geom;
           mesh.userData.viewerMaterials = createViewerMeshMaterials(mesh.material, sharedMaterial);
           if (!hasSourceMaterialTransparency(mesh.material)) {
             sourceMeshes.push(new THREE.Mesh(geom, mesh.material));
           }
           
           mesh.visible = false; // Hidden by default, batched mesh handles rendering
           mesh.castShadow = false;
           mesh.receiveShadow = false;
           mesh.material = (mesh.userData.viewerMaterials as ViewerMeshMaterials).highlight;
        }
      });
      
      if (sourceMeshes.length > 0) {
        try {
          // Chunk the geometry merge to prevent V8 Out of Memory crashes on massive assemblies
          const CHUNK_SIZE = 1000;
          const chunks: THREE.BufferGeometry[] = [];
          const hasAuthoredColors = sourceMeshes.some(mesh => hasAuthoredMaterialColor(mesh.material));
          
          for (let i = 0; i < sourceMeshes.length; i += CHUNK_SIZE) {
             const batch = buildViewerBatch(sourceMeshes.slice(i, i + CHUNK_SIZE), { useAuthoredColors: hasAuthoredColors });
             if (batch) {
               chunks.push(batch.mesh.geometry);
               if (Array.isArray(batch.mesh.material)) batch.mesh.material.forEach(mat => mat.dispose());
               else batch.mesh.material.dispose();
             }
          }
          
          const finalMergedGeom = BufferGeometryUtils.mergeGeometries(chunks, false);
          chunks.forEach(g => g.dispose()); // Free intermediate chunks
          
          if (finalMergedGeom) {
             const batchedMaterial = hasAuthoredColors
               ? new THREE.MeshStandardMaterial({
                   color: 0xffffff,
                   vertexColors: true,
                   metalness: 0.6,
                   roughness: 0.4,
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
      
      // Unpack the hierarchy
      setSceneGraph(model);
      setSelectedNodeId(null);
      finishLoad();
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
  }, [modelUrl, getAccessToken, renderQuality, clearCurrentModel, isActive]);

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
          // Temporarily unhide individual meshes for raycasting checks
          meshRef.current.traverse(c => {
            if (!isViewerBatchMesh(c) && (c as THREE.Mesh).isMesh) c.visible = true;
          });
          
          const intersects = raycaster
            .intersectObject(meshRef.current, true)
            .filter(intersection => !isViewerBatchMesh(intersection.object));
          
          // Re-hide them (they'll be unhidden by the selection effect if needed)
          meshRef.current.traverse(c => {
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
      const sharedMaterial = model.userData.sharedMat as THREE.Material | undefined;
      const highlightMaterial = model.userData.highlightMat as THREE.Material | undefined;
      const hasAppearanceOverrides = Object.values(appearanceByPath).some(appearance => appearance.hidden || appearance.transparent);
      const appearanceBatchKey = JSON.stringify(appearanceByPath);
      const externallySelectedIds = externalSelectedNodeIds ? new Set(externalSelectedNodeIds.filter(Boolean)) : null;
      const normalizedExternalIds = new Set([...(externallySelectedIds || new Set<string>())].map(normalizeExternalSelectionId).filter(Boolean));
      const externalSelection = externallySelectedIds?.size ? resolveExternalSelectionMeshes(model, externallySelectedIds) : null;
      const hasRenderableExternalSelection = Boolean(externalSelection?.hasSelection);
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

     if (hasAppearanceOverrides && model.userData.appearanceBatchKey !== appearanceBatchKey) {
        removeAppearanceBatch();

        const opaqueMeshes: THREE.Mesh[] = [];
        model.traverse((child) => {
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
              const geometry = child.userData.viewerBatchGeometry as THREE.BufferGeometry | undefined;
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
     } else if (!hasAppearanceOverrides) {
        removeAppearanceBatch();
     }
     
     // Reset batched mesh
     if (batchedMesh) batchedMesh.visible = !hasAppearanceOverrides && !hasRenderableExternalSelection;
     if (appearanceBatchMesh) appearanceBatchMesh.visible = hasAppearanceOverrides && !hasRenderableExternalSelection;
     
     // Evaluate visibility for individual meshes based on selection or isolation
     model.traverse((child) => {
        if (isViewerBatchMesh(child)) return;
        
        if ((child as THREE.Mesh).isMesh) {
           const mesh = child as THREE.Mesh;
           
           let isSelected = false;
           let isHidden = false;
           let isTransparent = false;
           const hasModelTransparency = hasSourceMaterialTransparency(mesh.userData.viewerSourceMaterial as THREE.Material | THREE.Material[] | undefined);
            let p: THREE.Object3D | null = child;

            while (p && p !== model) {
               if (isNodeSelected(p)) isSelected = true;
               const appearance = appearanceByPath[getSceneNodePathKey(model, p)];
               if (appearance?.hidden) isHidden = true;
              if (appearance?.transparent) isTransparent = true;
              p = p.parent;
           }
           
           if (hasRenderableExternalSelection) {
              mesh.visible = Boolean(externalSelection?.meshes.has(mesh)) && !isHidden;
           } else if (hasAppearanceOverrides) {
              mesh.visible = !isHidden && (isTransparent || isSelected || hasModelTransparency);
           } else {
              // In normal mode, only the selected parts are visible (as an overlay on the batched mesh)
              mesh.visible = isSelected || hasModelTransparency;
           }

           if (mesh.visible) {
              const viewerMaterials = mesh.userData.viewerMaterials as ViewerMeshMaterials | undefined;
              const shouldHighlightSelection = isSelected && !hasRenderableExternalSelection;
              if (isTransparent && shouldHighlightSelection && viewerMaterials) {
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

   }, [selectedNodeId, sceneGraph, appearanceByPath, renderQuality, externalSelectionKey]);

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
      
      {/* Overlay Status */}
      <div className="absolute top-4 left-4 z-10 bg-slate-950/80 backdrop-blur border border-slate-800 rounded-lg p-3 shadow-xl pointer-events-none flex flex-col gap-2">
        <div className="flex items-center justify-between gap-4">
          <div className="text-xs font-mono font-medium text-sky-400 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-sky-500 animate-pulse" />
            Extus Viewer
          </div>
          {projectName && (
            <div className="text-xs font-bold text-slate-300 bg-slate-800 px-2 py-0.5 rounded border border-slate-700">
              {projectName}
            </div>
          )}
          <button
            onClick={() => frameModelRoot(1.5)}
            className="pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border border-slate-700 bg-slate-800 text-slate-300 transition-colors hover:border-sky-500 hover:text-sky-300"
            title="Frame the whole model"
            aria-label="Frame the whole model"
          >
            Fit
          </button>
          <button 
            onClick={() => setRenderQuality(renderQuality === 'high' ? 'low' : 'high')}
            className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${renderQuality === 'high' ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
          >
            Visuals: {renderQuality === 'high' ? 'High' : 'Low'}
          </button>
          <button 
            onClick={() => setShowGrid(!showGrid)}
            className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${showGrid ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
          >
            Grid: {showGrid ? 'ON' : 'OFF'}
          </button>
          <button 
            onClick={() => setAutoRotate(!autoRotate)}
            className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${autoRotate ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
          >
            Rotate: {autoRotate ? 'ON' : 'OFF'}
          </button>
        </div>
        <div className="text-xs text-slate-400" aria-live="polite">
          {loadErrorText || (isModelLoading ? 'Loading model...' : statusText)}
        </div>
      </div>
      
      {/* 3D Canvas */}
      <div className="flex-1 relative" ref={containerRef}>
        <canvas ref={canvasRef} className="w-full h-full block outline-none" />
      </div>
    </div>
  );
};
