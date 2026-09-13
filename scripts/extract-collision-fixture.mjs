import fs from 'node:fs'
import path from 'node:path'
import * as THREE from '../ui/node_modules/three/build/three.module.js'

const [inputPath, outputPath] = process.argv.slice(2)
if (!inputPath || !outputPath) {
  throw new Error('Usage: node scripts/extract-collision-fixture.mjs <model.glb> <fixture.json>')
}

const content = fs.readFileSync(inputPath)
if (content.toString('ascii', 0, 4) !== 'glTF') throw new Error('Expected a binary glTF file')

let json
let binary
let offset = 12
while (offset < content.length) {
  const chunkLength = content.readUInt32LE(offset)
  const chunkType = content.readUInt32LE(offset + 4)
  const chunk = content.subarray(offset + 8, offset + 8 + chunkLength)
  if (chunkType === 0x4e4f534a) json = JSON.parse(chunk.toString('utf8').replace(/\0+$/u, ''))
  if (chunkType === 0x004e4942) binary = chunk
  offset += 8 + chunkLength
}
if (!json || !binary) throw new Error('GLB is missing JSON or binary data')

const componentReaders = {
  5120: DataView.prototype.getInt8,
  5121: DataView.prototype.getUint8,
  5122: DataView.prototype.getInt16,
  5123: DataView.prototype.getUint16,
  5125: DataView.prototype.getUint32,
  5126: DataView.prototype.getFloat32,
}
const componentSizes = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 }
const typeSizes = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }

const readAccessor = (accessorIndex) => {
  const accessor = json.accessors[accessorIndex]
  const view = json.bufferViews[accessor.bufferView]
  if (accessor.sparse) throw new Error(`Sparse accessor ${accessorIndex} is unsupported`)
  const componentSize = componentSizes[accessor.componentType]
  const componentCount = typeSizes[accessor.type]
  const reader = componentReaders[accessor.componentType]
  if (!componentSize || !componentCount || !reader) throw new Error(`Unsupported accessor ${accessorIndex}`)
  const stride = view.byteStride || componentSize * componentCount
  const start = (view.byteOffset || 0) + (accessor.byteOffset || 0)
  const data = new DataView(binary.buffer, binary.byteOffset, binary.byteLength)
  const values = new Array(accessor.count * componentCount)
  for (let item = 0; item < accessor.count; item += 1) {
    for (let component = 0; component < componentCount; component += 1) {
      values[item * componentCount + component] = reader.call(
        data,
        start + item * stride + component * componentSize,
        true,
      )
    }
  }
  return values
}

const localMatrix = (node) => {
  if (node.matrix) return new THREE.Matrix4().fromArray(node.matrix)
  return new THREE.Matrix4().compose(
    new THREE.Vector3().fromArray(node.translation || [0, 0, 0]),
    new THREE.Quaternion().fromArray(node.rotation || [0, 0, 0, 1]),
    new THREE.Vector3().fromArray(node.scale || [1, 1, 1]),
  )
}

const parents = new Map()
json.nodes.forEach((node, parentIndex) => {
  for (const childIndex of node.children || []) parents.set(childIndex, parentIndex)
})

const worldMatrix = (nodeIndex) => {
  const lineage = []
  let current = nodeIndex
  while (current !== undefined) {
    lineage.push(current)
    current = parents.get(current)
  }
  return lineage.reverse().reduce(
    (matrix, index) => matrix.multiply(localMatrix(json.nodes[index])),
    new THREE.Matrix4(),
  )
}

const findNode = (pattern) => {
  const matches = json.nodes
    .map((node, index) => ({ node, index }))
    .filter(({ node }) => pattern.test(node.name || ''))
  if (matches.length !== 1) {
    throw new Error(`Expected one node matching ${pattern}, found ${matches.length}`)
  }
  return matches[0]
}

const extractNode = (pattern, id) => {
  const { node, index } = findNode(pattern)
  const mesh = json.meshes[node.mesh]
  if (!mesh) throw new Error(`Node ${node.name} has no mesh`)
  const matrixWorld = worldMatrix(index).toArray()
  return {
    id,
    label: node.name,
    meshes: mesh.primitives.map((primitive, primitiveIndex) => ({
      id: `${id}-${primitiveIndex}`,
      positions: readAccessor(primitive.attributes.POSITION),
      indices: readAccessor(primitive.indices),
      matrixWorld,
    })),
  }
}

const extractPolicyNode = (pattern, id) => {
  const { node } = findNode(pattern)
  return { id, label: node.name }
}

const fixture = {
  schemaVersion: 1,
  source: {
    project: 'shed',
    designGitCommit: 'dab69a2',
    capturedAt: '2026-09-11',
    originalArtifactBytes: content.length,
    note: 'Exact rendered meshes extracted from the live demo GLB; source PDF files are not included.',
  },
  cases: [
    {
      id: 'ceiling-cutout-and-skylight-diffuser',
      expected: 'reject',
      rationale: 'The diffuser occupies the designed opening and must not be reported as penetrating the OSB frame.',
      minimumPenetrationMm: 1,
      sceneUnitsPerMillimeter: 0.001,
      components: [
        extractNode(/^TradeMaster OSB MR Ceiling Panel ICL01RP4-/, 'ceiling-panel'),
        extractNode(/^PERSPEX 4201 Opal White 3mm Internal Skylight Diffuser 6$/, 'skylight-diffuser'),
      ],
    },
  ],
  policyCases: [
    {
      id: 'custom-orb-left-lap',
      expected: 'ignore-pair',
      rationale: 'Matching Custom Orb sheets interlock by design.',
      components: [
        extractPolicyNode(/^__TERTIUS_COLLISION_GROUP__ roof-sheet-left :: Left Roof Sheet 1 /, 'left-roof-sheet-1'),
        extractPolicyNode(/^__TERTIUS_COLLISION_GROUP__ roof-sheet-left :: Left Roof Sheet 2 /, 'left-roof-sheet-2'),
      ],
    },
  ],
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true })
fs.writeFileSync(outputPath, `${JSON.stringify(fixture)}\n`)
console.log(`Wrote ${outputPath} from ${content.length} GLB bytes`)
