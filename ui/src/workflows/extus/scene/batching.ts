import * as THREE from 'three';
import * as BufferGeometryUtils from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import {
  getSceneNodePathKey,
  type SceneNodeAppearanceMap,
} from '../../shared/sceneNodeSelection';
import {
  colorFromMaterial,
  DEFAULT_MODEL_COLOR,
  geometryWithVertexColor,
  hasAuthoredMaterialColor,
} from './materials';

export type ViewerBatchOptions = {
  createMesh?: (geometry: THREE.BufferGeometry, material: THREE.Material) => THREE.Mesh;
  useAuthoredColors?: boolean;
};

export type ViewerBatch = {
  mesh: THREE.Mesh;
  usesAuthoredColors: boolean;
};

export function normalizeExternalSelectionId(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
}

export function matchesExternalSelection(
  object: THREE.Object3D,
  selectedIds: Set<string>,
  normalizedSelectedIds: Set<string>,
): boolean {
  return (
    selectedIds.has(object.uuid)
    || Boolean(object.userData?.tertiusGltfNodeId && selectedIds.has(String(object.userData.tertiusGltfNodeId)))
    || Boolean(object.name && selectedIds.has(object.name))
    || Boolean(object.name && normalizedSelectedIds.has(normalizeExternalSelectionId(object.name)))
  );
}

export function isViewerBatchMesh(object: THREE.Object3D): boolean {
  return object.name === 'TertiusBatchedMesh' || object.name === 'TertiusAppearanceBatchMesh';
}

export function getRenderableObjectBounds(object: THREE.Object3D): THREE.Box3 {
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
}

export function resolveExternalSelectionMeshes(model: THREE.Object3D, selectedIds: Set<string>) {
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
}

export function closestSelectableSceneNode(
  object: THREE.Object3D,
  root: THREE.Object3D,
): THREE.Object3D {
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

export function isViewerObjectHidden(
  root: THREE.Object3D,
  object: THREE.Object3D,
  appearanceByPath: SceneNodeAppearanceMap,
): boolean {
  let current: THREE.Object3D | null = object;
  while (current && current !== root) {
    if (appearanceByPath[getSceneNodePathKey(root, current)]?.hidden) return true;
    current = current.parent;
  }
  return false;
}

export function buildViewerBatch(
  meshes: THREE.Mesh[],
  options: ViewerBatchOptions = {},
): ViewerBatch | null {
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
