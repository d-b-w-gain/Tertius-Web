import type * as THREE from 'three';

export const SCENE_NODE_SELECTION_STORAGE_KEY = 'tertius_selected_node';
export const SCENE_NODE_APPEARANCE_STORAGE_KEY = 'tertius_scene_node_appearance';

export type SceneNodeAppearance = {
  hidden?: boolean;
  transparent?: boolean;
};

export type SceneNodeAppearanceMap = Record<string, SceneNodeAppearance>;

type StoredSceneNodeSelection = {
  version: 1;
  path: number[];
  name: string;
};

function parseSelection(value: string | null): StoredSceneNodeSelection | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<StoredSceneNodeSelection>;
    if (
      parsed.version === 1 &&
      Array.isArray(parsed.path) &&
      parsed.path.every(index => Number.isInteger(index) && index >= 0)
    ) {
      return {
        version: 1,
        path: parsed.path,
        name: typeof parsed.name === 'string' ? parsed.name : '',
      };
    }
  } catch {
    return null;
  }
  return null;
}

export function getSceneNodePath(root: THREE.Object3D | null, node: THREE.Object3D | null): number[] | null {
  if (!root || !node) return null;
  if (root === node) return [];

  const path: number[] = [];
  let current: THREE.Object3D | null = node;
  while (current && current !== root) {
    const parent: THREE.Object3D | null = current.parent;
    if (!parent) return null;
    const index = parent.children.indexOf(current);
    if (index < 0) return null;
    path.unshift(index);
    current = parent;
  }

  return current === root ? path : null;
}

export function getSceneNodePathKey(root: THREE.Object3D | null, node: THREE.Object3D | null): string {
  const path = getSceneNodePath(root, node);
  return path ? `path:${path.join('.')}` : `name:${node?.name || ''}`;
}

export function createSceneNodeSelectionValue(root: THREE.Object3D | null, node: THREE.Object3D): string {
  const path = getSceneNodePath(root, node);
  if (!path) return node.name || '';
  return JSON.stringify({
    version: 1,
    path,
    name: node.name || '',
  } satisfies StoredSceneNodeSelection);
}

export function resolveSceneNodeSelection(root: THREE.Object3D | null, value: string | null): THREE.Object3D | null {
  if (!root || !value) return null;

  const parsed = parseSelection(value);
  if (parsed) {
    let current: THREE.Object3D | undefined = root;
    for (const index of parsed.path) {
      current = current?.children[index];
      if (!current) break;
    }
    if (current) return current;
  }

  return root.getObjectByName(value) || null;
}

export function isSceneNodeSelectionMatch(
  root: THREE.Object3D | null,
  node: THREE.Object3D,
  value: string | null,
): boolean {
  if (!value) return false;

  const parsed = parseSelection(value);
  if (parsed) {
    const path = getSceneNodePath(root, node);
    return Boolean(path && path.length === parsed.path.length && path.every((index, i) => index === parsed.path[i]));
  }

  return Boolean(node.name && node.name === value);
}

export function readSceneNodeAppearanceMap(value: string | null): SceneNodeAppearanceMap {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value) as Record<string, Partial<SceneNodeAppearance>>;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};

    const next: SceneNodeAppearanceMap = {};
    for (const [key, appearance] of Object.entries(parsed)) {
      if (!key || !appearance || typeof appearance !== 'object') continue;
      next[key] = {
        hidden: appearance.hidden === true,
        transparent: appearance.transparent === true,
      };
    }
    return next;
  } catch {
    return {};
  }
}

export function writeSceneNodeAppearanceMap(appearanceByPath: SceneNodeAppearanceMap): void {
  const compact: SceneNodeAppearanceMap = {};
  for (const [key, appearance] of Object.entries(appearanceByPath)) {
    const next: SceneNodeAppearance = {};
    if (appearance.hidden) next.hidden = true;
    if (appearance.transparent) next.transparent = true;
    if (next.hidden || next.transparent) compact[key] = next;
  }

  if (Object.keys(compact).length === 0) {
    localStorage.removeItem(SCENE_NODE_APPEARANCE_STORAGE_KEY);
  } else {
    localStorage.setItem(SCENE_NODE_APPEARANCE_STORAGE_KEY, JSON.stringify(compact));
  }
}
