import * as THREE from 'three';

export const DEFAULT_MODEL_COLOR = 0x8b9bb4;

export type ViewerMeshMaterials = {
  base: THREE.Material | THREE.Material[];
  highlight: THREE.Material | THREE.Material[];
  transparent: THREE.Material | THREE.Material[];
  transparentHighlight: THREE.Material | THREE.Material[];
  collisionA: THREE.Material | THREE.Material[];
  collisionB: THREE.Material | THREE.Material[];
};

function materialList(material: THREE.Material | THREE.Material[]): THREE.Material[] {
  return Array.isArray(material) ? material : [material];
}

export function hasAuthoredMaterialColor(
  material: THREE.Material | THREE.Material[] | null | undefined,
): boolean {
  if (!material) return false;
  return materialList(material).some((mat) => mat.userData?.tertiusAuthoredColor === true && 'color' in mat);
}

export function hasSourceMaterialTransparency(
  material: THREE.Material | THREE.Material[] | null | undefined,
): boolean {
  if (!material) return false;
  return materialList(material).some((mat) => mat.transparent === true && 'opacity' in mat && mat.opacity < 1);
}

export function colorFromMaterial(
  material: THREE.Material | THREE.Material[] | null | undefined,
): THREE.Color | null {
  if (!material) return null;
  const authored = materialList(material).find((mat) => mat.userData?.tertiusAuthoredColor === true && 'color' in mat);
  const color = authored && 'color' in authored ? (authored as THREE.MeshStandardMaterial).color : null;
  return color ? color.clone() : null;
}

export function geometryWithVertexColor(
  geometry: THREE.BufferGeometry,
  color: THREE.Color,
): THREE.BufferGeometry {
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

export function cloneViewerMaterial(
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

export function createViewerMaterialVariant(
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

  const collisionA = createViewerMaterialVariant(baseSource, fallbackMaterial, (mat) => {
    if ('emissive' in mat) {
      (mat as THREE.MeshStandardMaterial).emissive.setHex(0xef4444);
      (mat as THREE.MeshStandardMaterial).emissiveIntensity = 0.9;
    }
    mat.polygonOffset = true;
    mat.polygonOffsetFactor = -1;
    mat.polygonOffsetUnits = -1;
  });
  const collisionB = createViewerMaterialVariant(baseSource, fallbackMaterial, (mat) => {
    if ('emissive' in mat) {
      (mat as THREE.MeshStandardMaterial).emissive.setHex(0xf59e0b);
      (mat as THREE.MeshStandardMaterial).emissiveIntensity = 0.9;
    }
    mat.polygonOffset = true;
    mat.polygonOffsetFactor = -1;
    mat.polygonOffsetUnits = -1;
  });

  return { base, highlight, transparent, transparentHighlight, collisionA, collisionB };
}

export function disposeMaterial(
  material: THREE.Material | THREE.Material[] | null | undefined,
): void {
  if (!material) return;
  if (Array.isArray(material)) material.forEach(mat => mat.dispose());
  else material.dispose();
}

export function disposeViewerMeshMaterials(materials: ViewerMeshMaterials | undefined): void {
  if (!materials) return;
  disposeMaterial(materials.base);
  disposeMaterial(materials.highlight);
  disposeMaterial(materials.transparent);
  disposeMaterial(materials.transparentHighlight);
  disposeMaterial(materials.collisionA);
  disposeMaterial(materials.collisionB);
}

export function disposeMesh(mesh: THREE.Mesh): void {
  mesh.geometry.dispose();
  disposeMaterial(mesh.material);
}

export function disposeObjectTree(object: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const collectMaterial = (material: THREE.Material | THREE.Material[] | null | undefined) => {
    if (!material) return;
    (Array.isArray(material) ? material : [material]).forEach(item => materials.add(item));
  };
  const collectViewerMaterials = (viewerMaterials: ViewerMeshMaterials | undefined) => {
    if (!viewerMaterials) return;
    collectMaterial(viewerMaterials.base);
    collectMaterial(viewerMaterials.highlight);
    collectMaterial(viewerMaterials.transparent);
    collectMaterial(viewerMaterials.transparentHighlight);
    collectMaterial(viewerMaterials.collisionA);
    collectMaterial(viewerMaterials.collisionB);
  };

  collectMaterial(object.userData?.sharedMat as THREE.Material | THREE.Material[] | undefined);
  collectMaterial(object.userData?.highlightMat as THREE.Material | THREE.Material[] | undefined);

  object.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      const mesh = child as THREE.Mesh;
      geometries.add(mesh.geometry);
      collectMaterial(mesh.material);
      collectMaterial(mesh.userData.viewerSourceMaterial as THREE.Material | THREE.Material[] | undefined);
      collectViewerMaterials(mesh.userData.viewerMaterials as ViewerMeshMaterials | undefined);
      const viewerGeometry = mesh.userData.viewerBatchGeometry as THREE.BufferGeometry | undefined;
      if (viewerGeometry) geometries.add(viewerGeometry);
    } else if ((child as THREE.Line).isLine || (child as THREE.LineSegments).isLineSegments) {
      const line = child as THREE.Line;
      geometries.add(line.geometry);
      collectMaterial(line.material);
    }
  });

  geometries.forEach(geometry => geometry.dispose());
  materials.forEach(material => material.dispose());
}
