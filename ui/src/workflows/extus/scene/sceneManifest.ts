import * as THREE from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';

/**
 * Per-component scenes (#314/#315).
 *
 * A compile with the component cache can publish a `tertius.scene.v1`
 * manifest beside its monolithic GLB: the same node tree, with each unit (a
 * cached part) taken from its own immutable GLB asset. Assembling it gives the
 * Object3D tree the monolithic GLB would have, so the viewer's selection,
 * colours, batching and source links work unchanged. Assets are cached for
 * the session by ID, so a one-part edit downloads and parses only the changed
 * parts' assets.
 */

export const SCENE_SCHEMA_VERSION = 'tertius.scene.v1';

export interface SceneManifestNode {
  name: string;
  /** Column-major 4x4 in glTF metres, local to the parent node. */
  matrix: number[];
  asset?: string;
  visual?: { source_call_ids?: unknown } | null;
  children: SceneManifestNode[];
}

export interface SceneManifest {
  schema_version: string;
  root: SceneManifestNode;
  source_map?: unknown;
  assets: string[];
}

export type AnnotateGltf = (
  root: THREE.Object3D,
  gltfJson: Record<string, unknown>,
  associations?: Map<THREE.Object3D, unknown>,
) => void;

interface ParsedAsset {
  /** The asset's root node, annotated; placed instances are clones. */
  template: THREE.Object3D;
}

export interface SceneAssetCache {
  get(assetId: string): Promise<ParsedAsset> | undefined;
  set(assetId: string, asset: Promise<ParsedAsset>): void;
  delete(assetId: string): void;
  keys(): IterableIterator<string>;
}

export function createSceneAssetCache(): SceneAssetCache {
  return new Map<string, Promise<ParsedAsset>>();
}

export function isSceneManifest(value: unknown): value is SceneManifest {
  const manifest = value as SceneManifest | null;
  return Boolean(
    manifest
      && manifest.schema_version === SCENE_SCHEMA_VERSION
      && manifest.root
      && typeof manifest.root.asset === 'string'
      && Array.isArray(manifest.assets),
  );
}

function setLocalMatrix(object: THREE.Object3D, matrix: number[]): void {
  object.matrix.fromArray(matrix);
  object.matrix.decompose(object.position, object.quaternion, object.scale);
}

/** Visual properties that make two materials render the same. */
function materialSignature(material: THREE.Material): string {
  const standard = material as THREE.MeshStandardMaterial;
  return JSON.stringify([
    material.type,
    standard.color?.getHexString?.(),
    standard.emissive?.getHexString?.(),
    standard.metalness,
    standard.roughness,
    material.opacity,
    material.transparent,
    material.side,
    material.vertexColors,
    material.alphaTest,
    material.depthWrite,
    standard.map ? standard.map.uuid : null,
    material.userData,
  ]);
}

/**
 * Parse and annotate one asset: node metadata from its own glTF JSON, and an
 * instancing key per mesh from its content digest so equal geometry is
 * instanced across assets (glTF mesh indices only identify meshes within one
 * asset).
 */
async function parseAsset(
  assetId: string,
  buffer: ArrayBuffer,
  parse: (buffer: ArrayBuffer) => Promise<GLTF>,
  annotate: AnnotateGltf,
  materials: Map<string, THREE.Material>,
): Promise<ParsedAsset> {
  const gltf = await parse(buffer);
  const parser = gltf.parser as unknown as {
    json?: { meshes?: Array<{ extras?: { tertiusMeshDigest?: unknown } }> };
    associations?: Map<THREE.Object3D, { meshes?: number; primitives?: number }>;
  };
  annotate(gltf.scene, (parser.json || {}) as Record<string, unknown>, parser.associations);
  const meshes = parser.json?.meshes || [];
  gltf.scene.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const meshId = mesh.userData.tertiusGltfMeshId;
    const digest = typeof meshId === 'number' ? meshes[meshId]?.extras?.tertiusMeshDigest : undefined;
    mesh.userData.tertiusGeometryKey = typeof digest === 'string'
      ? `digest:${digest}:primitive:${mesh.userData.tertiusGltfPrimitiveId ?? 0}`
      : `asset:${assetId}:mesh:${String(meshId)}:primitive:${mesh.userData.tertiusGltfPrimitiveId ?? 0}`;
    // One material object per distinct look, shared by every asset, so the
    // viewer's batching (keyed on material identity) spans assets too.
    const shared = (material: THREE.Material) => {
      const signature = materialSignature(material);
      const existing = materials.get(signature);
      if (existing) return existing;
      materials.set(signature, material);
      return material;
    };
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(shared) : shared(mesh.material);
  });
  const template = gltf.scene.children[0];
  if (!template) throw new Error(`scene asset ${assetId} has no root node`);
  return { template };
}

export interface AssembleOptions {
  fetchAsset: (assetId: string) => Promise<ArrayBuffer>;
  parse: (buffer: ArrayBuffer) => Promise<GLTF>;
  annotate: AnnotateGltf;
  cache: SceneAssetCache;
  materials: Map<string, THREE.Material>;
}

export interface AssembledScene {
  scene: THREE.Group;
  /** Assets downloaded for this scene (not already cached). */
  fetched: number;
  assets: number;
}

/** Build the scene a manifest describes, fetching only uncached assets. */
export async function assembleScene(manifest: SceneManifest, options: AssembleOptions): Promise<AssembledScene> {
  let fetched = 0;
  const load = (assetId: string): Promise<ParsedAsset> => {
    let pending = options.cache.get(assetId);
    if (!pending) {
      fetched += 1;
      pending = options.fetchAsset(assetId).then((buffer) =>
        parseAsset(assetId, buffer, options.parse, options.annotate, options.materials),
      );
      pending.catch(() => options.cache.delete(assetId));
      options.cache.set(assetId, pending);
    }
    return pending;
  };
  // Keep only this scene's assets: the cache must not grow with every edit
  // of a long session. Earlier scenes' objects are disposed by the viewer.
  const wanted = new Set(manifest.assets);
  for (const assetId of [...options.cache.keys()]) {
    if (!wanted.has(assetId)) options.cache.delete(assetId);
  }
  const parsed = new Map<string, ParsedAsset>();
  await Promise.all(
    manifest.assets.map(async (assetId) => {
      parsed.set(assetId, await load(assetId));
    }),
  );

  let instance = 0;
  const place = (entry: SceneManifestNode): THREE.Object3D => {
    const asset = parsed.get(entry.asset as string);
    if (!asset) throw new Error(`scene asset ${entry.asset} is missing`);
    const prefix = `i${instance}:`;
    instance += 1;
    const object = asset.template.clone(true);
    object.traverse((node) => {
      if (node.userData.tertiusGltfNodeId !== undefined) {
        node.userData.tertiusGltfNodeId = prefix + String(node.userData.tertiusGltfNodeId);
      }
    });
    setLocalMatrix(object, entry.matrix);
    fill(entry, object);
    return object;
  };

  // The asset's own node children are the manifest children without an asset,
  // in order, after any mesh primitives the loader made children of the node;
  // units nested here come from their own assets, at their manifest position.
  const fill = (entry: SceneManifestNode, object: THREE.Object3D): void => {
    const own = entry.children.filter((child) => child.asset === undefined).length;
    const primitives = object.children.slice(0, object.children.length - own);
    const nodeChildren = object.children.slice(object.children.length - own);
    const ordered: THREE.Object3D[] = [...primitives];
    let next = 0;
    for (const child of entry.children) {
      if (child.asset !== undefined) {
        ordered.push(place(child));
      } else {
        const node = nodeChildren[next];
        next += 1;
        if (!node) throw new Error(`scene asset is missing node ${child.name}`);
        const ids = child.visual?.source_call_ids;
        if (Array.isArray(ids) && ids.length) node.userData.tertiusSourceCallIds = ids.map(String).filter(Boolean);
        fill(child, node);
        ordered.push(node);
      }
    }
    const ids = entry.visual?.source_call_ids;
    if (Array.isArray(ids) && ids.length) object.userData.tertiusSourceCallIds = ids.map(String).filter(Boolean);
    object.clear();
    ordered.forEach((child) => object.add(child));
  };

  const scene = new THREE.Group();
  scene.add(place(manifest.root));
  if (manifest.source_map && typeof manifest.source_map === 'object') {
    scene.userData.tertiusSourceMap = manifest.source_map;
  }
  return { scene, fetched, assets: manifest.assets.length };
}
