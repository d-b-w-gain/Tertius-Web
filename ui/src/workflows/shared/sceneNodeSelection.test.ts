import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import {
  createSceneNodeSelectionValue,
  getSceneNodePathKey,
  isSceneNodeSelectionMatch,
  readSceneNodeAppearanceMap,
  resolveSceneNodeSelection,
  writeSceneNodeAppearanceMap,
} from './sceneNodeSelection';

describe('sceneNodeSelection', () => {
  function buildScene() {
    const root = new THREE.Object3D();
    root.name = 'Root';

    const first = new THREE.Group();
    first.name = 'Bracket';
    const second = new THREE.Group();
    second.name = 'Bracket';
    const child = new THREE.Group();
    child.name = 'Anchor';

    root.add(first);
    root.add(second);
    second.add(child);

    return { root, first, second, child };
  }

  it('serializes selections by scene path so duplicate names resolve to the intended node', () => {
    const { root, first, second } = buildScene();

    const value = createSceneNodeSelectionValue(root, second);

    expect(resolveSceneNodeSelection(root, value)).toBe(second);
    expect(isSceneNodeSelectionMatch(root, second, value)).toBe(true);
    expect(isSceneNodeSelectionMatch(root, first, value)).toBe(false);
  });

  it('keeps legacy name-only selections working', () => {
    const { root, child } = buildScene();

    expect(resolveSceneNodeSelection(root, 'Anchor')).toBe(child);
    expect(isSceneNodeSelectionMatch(root, child, 'Anchor')).toBe(true);
  });

  it('uses stable path keys for Artus tree highlighting', () => {
    const { root, child } = buildScene();

    expect(getSceneNodePathKey(root, child)).toBe('path:1.0');
  });

  it('compacts scene appearance state when writing to local storage', () => {
    writeSceneNodeAppearanceMap({
      'path:0': { hidden: true, transparent: false },
      'path:1': { hidden: false, transparent: false },
      'path:2': { transparent: true },
    });

    expect(readSceneNodeAppearanceMap(localStorage.getItem('tertius_scene_node_appearance'))).toEqual({
      'path:0': { hidden: true, transparent: false },
      'path:2': { hidden: false, transparent: true },
    });
  });
});
