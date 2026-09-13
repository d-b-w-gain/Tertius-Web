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

export type ViewerInstanceCandidate = {
  source: THREE.Mesh;
  geometry: THREE.BufferGeometry;
  sourceMaterial: THREE.Material | THREE.Material[];
  matrix: THREE.Matrix4;
  geometryKey?: string;
};

export type ViewerInstanceBuildOptions = {
  minimumInstances?: number;
  createMesh?: (
    geometry: THREE.BufferGeometry,
    sourceMaterial: THREE.Material | THREE.Material[],
    count: number,
  ) => THREE.InstancedMesh;
};

export type ViewerInstanceBuild = {
  meshes: THREE.InstancedMesh[];
  leftovers: ViewerInstanceCandidate[];
  instanceCount: number;
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
  return object.userData?.tertiusViewerBatch === true
    || object.name === 'TertiusBatchedMesh'
    || object.name === 'TertiusAppearanceBatchMesh';
}

function materialIdentity(material: THREE.Material | THREE.Material[]): string {
  return (Array.isArray(material) ? material : [material]).map(item => item.uuid).join(',');
}

/**
 * Build GPU instance batches only from explicit GLTF geometry identities.
 *
 * The server-side GLB optimiser makes repeated primitives share a mesh index;
 * GLTFLoader exposes that index through its association map. Requiring that
 * identity here prevents equal-looking but semantically distinct geometry from
 * being combined by a heuristic. Mirrored matrices remain on the established
 * geometry-baking path because Three.js does not support negatively scaled
 * InstancedMesh transforms.
 */
export function buildViewerInstances(
  candidates: ViewerInstanceCandidate[],
  options: ViewerInstanceBuildOptions = {},
): ViewerInstanceBuild {
  const minimumInstances = Math.max(2, Math.floor(options.minimumInstances ?? 2));
  const buckets = new Map<string, ViewerInstanceCandidate[]>();
  const leftovers: ViewerInstanceCandidate[] = [];

  candidates.forEach((candidate) => {
    if (!candidate.geometryKey || candidate.matrix.determinant() <= 0) {
      leftovers.push(candidate);
      return;
    }
    const key = `${candidate.geometryKey}|${materialIdentity(candidate.sourceMaterial)}`;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(candidate);
    else buckets.set(key, [candidate]);
  });

  const meshes: THREE.InstancedMesh[] = [];
  let instanceCount = 0;
  buckets.forEach((bucket, key) => {
    if (bucket.length < minimumInstances) {
      leftovers.push(...bucket);
      return;
    }

    const first = bucket[0]!;
    const mesh = options.createMesh?.(
      first.geometry,
      first.sourceMaterial,
      bucket.length,
    ) ?? new THREE.InstancedMesh(first.geometry, first.sourceMaterial, bucket.length);
    bucket.forEach((candidate, index) => mesh.setMatrixAt(index, candidate.matrix));
    mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingBox();
    mesh.computeBoundingSphere();
    mesh.name = `TertiusInstancedMesh-${meshes.length + 1}`;
    mesh.userData.tertiusViewerBatch = true;
    mesh.userData.viewerInstanceKey = key;
    mesh.userData.viewerInstanceSources = bucket.map(candidate => candidate.source);
    meshes.push(mesh);
    instanceCount += bucket.length;
  });

  return { meshes, leftovers, instanceCount };
}

function reverseTriangleWinding(geometry: THREE.BufferGeometry): void {
  const index = geometry.getIndex();
  if (index) {
    for (let offset = 0; offset + 2 < index.count; offset += 3) {
      const second = index.getX(offset + 1);
      index.setX(offset + 1, index.getX(offset + 2));
      index.setX(offset + 2, second);
    }
    index.needsUpdate = true;
    return;
  }

  // Non-indexed geometry stores each triangle as three consecutive vertices.
  // Swap the final two vertices, including normals/UVs/colours, to keep every
  // attribute aligned with its position.
  for (const attribute of Object.values(geometry.attributes)) {
    for (let offset = 0; offset + 2 < attribute.count; offset += 3) {
      for (let component = 0; component < attribute.itemSize; component += 1) {
        const second = attribute.getComponent(offset + 1, component);
        attribute.setComponent(offset + 1, component, attribute.getComponent(offset + 2, component));
        attribute.setComponent(offset + 2, component, second);
      }
    }
    attribute.needsUpdate = true;
  }
}

/**
 * Bake an instance transform into viewer geometry without losing mirrored
 * components to front-face culling. Three.js normally compensates for a
 * negative object transform at draw time; batching removes that object-level
 * signal, so the triangle winding must be corrected before the merge.
 */
export function applyViewerGeometryTransform(
  geometry: THREE.BufferGeometry,
  transform: THREE.Matrix4,
): THREE.BufferGeometry {
  if (transform.determinant() < 0) reverseTriangleWinding(geometry);
  geometry.applyMatrix4(transform);
  return geometry;
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
    if (current.name && !isViewerBatchMesh(current) && isAssemblyNode) return current;
    if (current.name && !isViewerBatchMesh(current)) fallback = current;
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
        metalness: 0.15,
        roughness: 0.72,
        side: THREE.FrontSide,
      })
    : new THREE.MeshStandardMaterial({
        color: DEFAULT_MODEL_COLOR,
        metalness: 0.15,
        roughness: 0.72,
        side: THREE.FrontSide,
      });

  return {
    mesh: (options.createMesh ?? ((geometry, meshMaterial) => new THREE.Mesh(geometry, meshMaterial)))(mergedGeometry, material),
    usesAuthoredColors,
  };
}
