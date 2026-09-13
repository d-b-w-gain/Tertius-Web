import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

if (!globalThis.ProgressEvent) {
  globalThis.ProgressEvent = class ProgressEvent extends Event {
    constructor(type, init = {}) {
      super(type)
      this.lengthComputable = Boolean(init.lengthComputable)
      this.loaded = Number(init.loaded || 0)
      this.total = Number(init.total || 0)
    }
  }
}

const inputPath = process.argv[2]
if (!inputPath) {
  console.error('Usage: node scripts/measure-glb-instancing.mjs <model.glb>')
  process.exitCode = 2
} else {
  const absolutePath = resolve(inputPath)
  const bytes = await readFile(absolutePath)
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
  const gltf = await new Promise((resolveGltf, reject) => {
    new GLTFLoader().parse(buffer, '', resolveGltf, reject)
  })

  gltf.scene.updateMatrixWorld(true)
  const associations = gltf.parser.associations
  const buckets = new Map()
  let renderedMeshes = 0
  let eligibleMeshes = 0
  let fallbackMeshes = 0

  gltf.scene.traverse((object) => {
    if (!object.isMesh) return
    renderedMeshes += 1
    const association = associations.get(object)
    const materials = Array.isArray(object.material) ? object.material : [object.material]
    const isTransparent = materials.some(material => material.transparent && material.opacity < 1)
    const supportsInstances = !object.isSkinnedMesh
      && Object.keys(object.geometry.morphAttributes).length === 0
      && object.matrixWorld.determinant() > 0
      && !isTransparent
      && typeof association?.meshes === 'number'
    if (!supportsInstances) {
      fallbackMeshes += 1
      return
    }

    eligibleMeshes += 1
    const materialKey = materials.map(material => material.uuid).join(',')
    const key = `mesh:${association.meshes}:primitive:${association.primitives ?? 0}|${materialKey}`
    buckets.set(key, (buckets.get(key) || 0) + 1)
  })

  const repeatedBuckets = [...buckets.values()].filter(count => count >= 2)
  const instances = repeatedBuckets.reduce((total, count) => total + count, 0)
  const compatibleUniqueMeshes = eligibleMeshes - instances
  const compatibilityMeshes = fallbackMeshes + compatibleUniqueMeshes
  const estimatedViewerDraws = repeatedBuckets.length + compatibilityMeshes
  console.log(JSON.stringify({
    file: absolutePath,
    bytes: bytes.byteLength,
    renderedMeshes,
    gpuInstanceBatches: repeatedBuckets.length,
    gpuInstances: instances,
    compatibilityMeshes,
    estimatedViewerDraws,
    drawObjectReductionPercent: Number((
      (1 - estimatedViewerDraws / Math.max(1, renderedMeshes)) * 100
    ).toFixed(1)),
  }, null, 2))
}
