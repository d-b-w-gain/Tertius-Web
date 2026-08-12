import maplibregl, { type CustomLayerInterface, type Map as MapLibreMap } from 'maplibre-gl'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

import { apiFetch } from '../../api/client'


type CandidateLayerOptions = {
  id: string
  map: MapLibreMap
  modelUrl: string
  getAccessToken: () => Promise<string>
  footprintLengthM: number
  footprintWidthM: number
  representation: CandidateRepresentation
  getPlacement: () => {
    longitude: number
    latitude: number
    frontBearingDegrees: number
  }
}

export type CandidateUpAxis = 'x' | 'y' | 'z'
export type CandidateRepresentation = 'envelope' | 'full'

export type CandidateModelPlacement = {
  upAxis: CandidateUpAxis
  sizeM: THREE.Vector3
}

function disposeObject(root: THREE.Object3D) {
  const geometries = new Set<THREE.BufferGeometry>()
  const materials = new Set<THREE.Material>()
  const textures = new Set<THREE.Texture>()
  root.traverse((child) => {
    if (!(child as THREE.Mesh).isMesh) return
    const mesh = child as THREE.Mesh
    geometries.add(mesh.geometry)
    const meshMaterials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    meshMaterials.forEach((material) => {
      materials.add(material)
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) textures.add(value)
      })
    })
  })
  textures.forEach((texture) => texture.dispose())
  materials.forEach((material) => material.dispose())
  geometries.forEach((geometry) => geometry.dispose())
}

export function placeCandidateModelOnSite(
  model: THREE.Object3D,
  footprintLengthM: number,
  footprintWidthM: number,
) : CandidateModelPlacement {
  // glTF defines +Y as up. Exporters such as Open CASCADE encode any CAD
  // Z-up conversion in the asset's root node, which GLTFLoader preserves.
  // The map's altitude axis is +Z, so rotate the loaded asset from Y-up to
  // Z-up without trying to infer orientation from its overall proportions.
  model.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2)
  model.updateMatrixWorld(true)
  let bounds = new THREE.Box3().setFromObject(model)
  let size = bounds.getSize(new THREE.Vector3())

  if (![size.x, size.y, size.z].every((value) => Number.isFinite(value) && value > 0)) {
    throw new Error('Candidate model has invalid geometry bounds')
  }

  // Match the model's long horizontal side to the nominated long footprint
  // side, but retain the GLB's native metre scale.
  const footprintIsLongerOnX = footprintLengthM >= footprintWidthM
  const modelIsLongerOnX = size.x >= size.y
  if (footprintIsLongerOnX !== modelIsLongerOnX) {
    model.quaternion.premultiply(
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2),
    )
    model.updateMatrixWorld(true)
    bounds = new THREE.Box3().setFromObject(model)
    size = bounds.getSize(new THREE.Vector3())
  }

  const center = bounds.getCenter(new THREE.Vector3())
  model.position.x -= center.x
  model.position.y -= center.y
  model.position.z -= bounds.min.z
  model.updateMatrixWorld(true)
  return { upAxis: 'y', sizeM: size }
}

function parseModel(buffer: ArrayBuffer) {
  return new Promise<THREE.Object3D>((resolve, reject) => {
    new GLTFLoader().parse(
      buffer,
      '',
      (gltf) => resolve(gltf.scene),
      (error) => reject(error),
    )
  })
}

function representationTags(object: THREE.Object3D): string[] {
  const direct = object.userData?.tertius_representation
  const nested = object.userData?.tertius?.representations
  return [direct, ...(Array.isArray(nested) ? nested : [])]
    .flatMap((value) => typeof value === 'string' ? value.split(/[\s,]+/) : [])
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean)
}

export function applyCandidateRepresentation(
  root: THREE.Object3D,
  representation: CandidateRepresentation,
): number {
  const meshes: THREE.Mesh[] = []
  root.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) meshes.push(child as THREE.Mesh)
  })
  if (representation === 'full') {
    meshes.forEach((mesh) => { mesh.visible = true })
    return meshes.length
  }

  const envelopePattern = /(^|[_\s-])(site[_\s-]?envelope|cladding|flashing|sheet|roof|wall|door|window|gutter|downpipe)([_\s-]|$)/i
  const selected = meshes.filter((mesh) => {
    const tags: string[] = []
    const names: string[] = []
    let current: THREE.Object3D | null = mesh
    while (current && current !== root.parent) {
      tags.push(...representationTags(current))
      if (current.name) names.push(current.name)
      current = current.parent
    }
    return tags.includes('site_envelope') || envelopePattern.test(names.join(' '))
  })
  // Older designs may not yet carry semantic node names.  Showing the full
  // candidate is safer than silently presenting an empty site representation.
  if (selected.length === 0) {
    meshes.forEach((mesh) => { mesh.visible = true })
    return meshes.length
  }
  const selectedSet = new Set(selected)
  meshes.forEach((mesh) => { mesh.visible = selectedSet.has(mesh) })
  return selected.length
}

export async function loadCandidateDesignLayer(options: CandidateLayerOptions) {
  const response = await apiFetch(options.modelUrl, options.getAccessToken)
  if (!response.ok) throw new Error(`Candidate model returned ${response.status}`)
  const asset = await parseModel(await response.arrayBuffer())
  const model = new THREE.Group()
  model.add(asset)
  applyCandidateRepresentation(model, options.representation)
  const placement = placeCandidateModelOnSite(
    model,
    options.footprintLengthM,
    options.footprintWidthM,
  )

  const scene = new THREE.Scene()
  scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 2.1))
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.4)
  keyLight.position.set(-35, -25, 70)
  scene.add(keyLight)
  scene.add(model)
  const camera = new THREE.Camera()
  let renderer: THREE.WebGLRenderer | null = null
  let disposed = false
  const dispose = () => {
    if (disposed) return
    disposed = true
    renderer?.setAnimationLoop(null)
    renderer?.renderLists.dispose()
    renderer?.dispose()
    disposeObject(model)
    scene.remove(model)
    scene.clear()
    renderer = null
  }

  const layer: CustomLayerInterface = {
    id: options.id,
    type: 'custom',
    renderingMode: '3d',
    onAdd(map, gl) {
      renderer = new THREE.WebGLRenderer({
        canvas: map.getCanvas(),
        context: gl,
        antialias: true,
      })
      renderer.autoClear = false
    },
    render(_gl, args) {
      if (!renderer) return
      const placement = options.getPlacement()
      const altitude = options.map.queryTerrainElevation([
        placement.longitude,
        placement.latitude,
      ]) ?? 0
      const origin = maplibregl.MercatorCoordinate.fromLngLat(
        [placement.longitude, placement.latitude],
        altitude,
      )
      const mapMatrix = new THREE.Matrix4().fromArray(
        args.defaultProjectionData.mainMatrix,
      )
      const modelMatrix = new THREE.Matrix4()
        .makeTranslation(origin.x, origin.y, origin.z)
        .scale(new THREE.Vector3(
          origin.meterInMercatorCoordinateUnits(),
          -origin.meterInMercatorCoordinateUnits(),
          origin.meterInMercatorCoordinateUnits(),
        ))
        .multiply(new THREE.Matrix4().makeRotationZ(
          -placement.frontBearingDegrees * Math.PI / 180,
        ))
      camera.projectionMatrix = mapMatrix.multiply(modelMatrix)
      renderer.resetState()
      renderer.render(scene, camera)
    },
    onRemove() {
      dispose()
    },
  }
  return { layer, dispose, ...placement }
}
