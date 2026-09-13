import { describe, expect, it } from 'vitest';
import * as THREE from 'three';

import { buildViewerInstances, isViewerBatchMesh } from './batching';

describe('buildViewerInstances', () => {
  it('instances repeated geometry while leaving unique geometry for batching', () => {
    const sharedGeometry = new THREE.BoxGeometry(1, 1, 1);
    const uniqueGeometry = new THREE.BoxGeometry(2, 1, 1);
    const material = new THREE.MeshStandardMaterial();
    const firstMatrix = new THREE.Matrix4().makeTranslation(1, 2, 3);
    const secondMatrix = new THREE.Matrix4().makeTranslation(4, 5, 6);

    const result = buildViewerInstances([
      { geometry: sharedGeometry, material, matrix: firstMatrix },
      { geometry: sharedGeometry, material, matrix: secondMatrix },
      { geometry: uniqueGeometry, material, matrix: new THREE.Matrix4() },
    ]);

    expect(result.meshes).toHaveLength(1);
    expect(result.meshes[0]!.count).toBe(2);
    expect(isViewerBatchMesh(result.meshes[0]!)).toBe(true);
    expect(result.leftovers).toHaveLength(1);
    expect(result.leftovers[0]!.geometry).toBe(uniqueGeometry);

    const matrix = new THREE.Matrix4();
    result.meshes[0]!.getMatrixAt(1, matrix);
    expect(matrix.elements).toEqual(secondMatrix.elements);
  });

  it('does not instance equal-looking geometry objects that are not shared', () => {
    const material = new THREE.MeshStandardMaterial();
    const result = buildViewerInstances([
      { geometry: new THREE.BoxGeometry(1, 1, 1), material, matrix: new THREE.Matrix4() },
      { geometry: new THREE.BoxGeometry(1, 1, 1), material, matrix: new THREE.Matrix4() },
    ]);

    expect(result.meshes).toHaveLength(0);
    expect(result.leftovers).toHaveLength(2);
  });
});
