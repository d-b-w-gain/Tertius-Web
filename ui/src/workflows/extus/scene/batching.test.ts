import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';

import {
  buildViewerInstances,
  isViewerBatchMesh,
  updateViewerInstanceAppearance,
} from './batching';

function candidate(
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  matrix: THREE.Matrix4,
  geometryKey?: string,
) {
  const source = new THREE.Mesh(geometry, material);
  source.userData.viewerBatchMatrix = matrix;
  return {
    source,
    geometry,
    sourceMaterial: material,
    matrix,
    geometryKey,
  };
}

describe('buildViewerInstances', () => {
  it('creates one GPU draw from repeated server-identified GLTF geometry', () => {
    const geometry = new THREE.BoxGeometry(1, 1, 1);
    const material = new THREE.MeshStandardMaterial();
    const firstMatrix = new THREE.Matrix4().makeTranslation(1, 2, 3);
    const secondMatrix = new THREE.Matrix4().makeTranslation(4, 5, 6);
    const createMesh = vi.fn((
      batchGeometry: THREE.BufferGeometry,
      batchMaterial: THREE.Material | THREE.Material[],
      count: number,
    ) => (
      new THREE.InstancedMesh(batchGeometry, batchMaterial, count)
    ));

    const result = buildViewerInstances([
      candidate(geometry, material, firstMatrix, 'mesh:7:primitive:0'),
      candidate(geometry, material, secondMatrix, 'mesh:7:primitive:0'),
    ], { createMesh });

    expect(createMesh).toHaveBeenCalledWith(geometry, material, 2);
    expect(result.instanceCount).toBe(2);
    expect(result.leftovers).toHaveLength(0);
    expect(result.meshes).toHaveLength(1);
    expect(isViewerBatchMesh(result.meshes[0]!)).toBe(true);

    const matrix = new THREE.Matrix4();
    result.meshes[0]!.getMatrixAt(1, matrix);
    expect(matrix.elements).toEqual(secondMatrix.elements);
  });

  it('does not combine candidates without an explicit GLTF geometry identity', () => {
    const geometry = new THREE.BoxGeometry(1, 1, 1);
    const material = new THREE.MeshStandardMaterial();
    const result = buildViewerInstances([
      candidate(geometry, material, new THREE.Matrix4()),
      candidate(geometry, material, new THREE.Matrix4()),
    ]);

    expect(result.meshes).toHaveLength(0);
    expect(result.leftovers).toHaveLength(2);
  });

  it('keeps different materials and mirrored transforms out of the same batch', () => {
    const geometry = new THREE.BoxGeometry(1, 1, 1);
    const steel = new THREE.MeshStandardMaterial({ color: 0x888888 });
    const paint = new THREE.MeshStandardMaterial({ color: 0xff0000 });
    const result = buildViewerInstances([
      candidate(geometry, steel, new THREE.Matrix4(), 'mesh:7:primitive:0'),
      candidate(geometry, steel, new THREE.Matrix4().makeScale(-1, 1, 1), 'mesh:7:primitive:0'),
      candidate(geometry, paint, new THREE.Matrix4(), 'mesh:7:primitive:0'),
    ]);

    expect(result.meshes).toHaveLength(0);
    expect(result.leftovers).toHaveLength(3);
  });

  it('masks only overridden instances and restores their original matrices', () => {
    const root = new THREE.Group();
    const geometry = new THREE.BoxGeometry(1, 1, 1);
    const material = new THREE.MeshStandardMaterial();
    const first = candidate(
      geometry,
      material,
      new THREE.Matrix4().makeTranslation(1, 0, 0),
      'mesh:7:primitive:0',
    );
    const second = candidate(
      geometry,
      material,
      new THREE.Matrix4().makeTranslation(2, 0, 0),
      'mesh:7:primitive:0',
    );
    root.add(first.source, second.source);
    const result = buildViewerInstances([first, second]);
    const instanceMesh = result.meshes[0]!;
    const matrix = new THREE.Matrix4();

    updateViewerInstanceAppearance(root, instanceMesh, {
      'path:1': { hidden: true },
    });
    instanceMesh.getMatrixAt(0, matrix);
    expect(matrix.elements).toEqual(first.matrix.elements);
    instanceMesh.getMatrixAt(1, matrix);
    expect(matrix.determinant()).toBe(0);

    updateViewerInstanceAppearance(root, instanceMesh, {});
    instanceMesh.getMatrixAt(1, matrix);
    expect(matrix.elements).toEqual(second.matrix.elements);
  });
});
