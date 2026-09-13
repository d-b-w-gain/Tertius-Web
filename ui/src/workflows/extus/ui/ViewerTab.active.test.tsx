import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  detectModelArtifactFormat,
  ModelViewerCanvas,
  ViewerTab,
  structuralCheckColor,
  structuralEvidenceColor,
  structuralRestraintColor,
} from './ViewerTab'
import { ViewerControls } from './ViewerControls'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  rendererSetSize: vi.fn(),
  gltfParse: vi.fn(),
  stlParse: vi.fn(),
}))

vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'authenticated', getAccessToken: mocks.getAccessToken }),
}))
vi.mock('three/examples/jsm/controls/OrbitControls.js', () => ({
  OrbitControls: class {
    autoRotate = false
    autoRotateSpeed = 0
    dampingFactor = 0
    enableDamping = false
    addEventListener = vi.fn()
    removeEventListener = vi.fn()
    update = vi.fn()
  },
}))
vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({
  GLTFLoader: class {
    parse = mocks.gltfParse
  },
}))
vi.mock('three/examples/jsm/loaders/STLLoader.js', () => ({
  STLLoader: class {
    parse = mocks.stlParse
  },
}))
vi.mock('three/examples/jsm/utils/BufferGeometryUtils.js', () => ({
  mergeGeometries: vi.fn(),
}))
vi.mock('three', () => {
  class Object3D {
    children: Object3D[] = []
    name = ''
    visible = true
    add(child: Object3D) {
      this.children.push(child)
    }
    remove(child: Object3D) {
      this.children = this.children.filter((candidate) => candidate !== child)
    }
    getObjectByName(name: string): Object3D | undefined {
      return this.children.find((child) => child.name === name)
    }
    traverse(callback: (child: Object3D) => void) {
      callback(this)
      for (const child of this.children) child.traverse(callback)
    }
  }

  class Camera extends Object3D {
    aspect = 1
    position = { set: vi.fn() }
    up = { set: vi.fn() }
    lookAt = vi.fn()
    updateProjectionMatrix = vi.fn()
  }

  return {
    Object3D,
    Scene: class extends Object3D {
      background: unknown
    },
    Color: class {
      value: number
      constructor(value: number) {
        this.value = value
      }
    },
    PerspectiveCamera: Camera,
    WebGLRenderer: class {
      shadowMap = {}
      toneMapping = 0
      toneMappingExposure = 0
      constructor() {}
      setSize = mocks.rendererSetSize
      setPixelRatio = vi.fn()
      render = vi.fn()
      dispose = vi.fn()
    },
    AmbientLight: class extends Object3D {
      isLight = true
    },
    HemisphereLight: class extends Object3D {
      isLight = true
      position = { set: vi.fn() }
    },
    DirectionalLight: class extends Object3D {
      isLight = true
      castShadow = false
      position = { set: vi.fn() }
      shadow = { mapSize: { width: 0, height: 0 }, bias: 0 }
    },
    GridHelper: class extends Object3D {
      rotation = { x: 0 }
      scale = { set: vi.fn() }
    },
    AxesHelper: class extends Object3D {
      scale = { set: vi.fn() }
    },
    PCFShadowMap: 1,
    ACESFilmicToneMapping: 2,
    FrontSide: 3,
    MeshStandardMaterial: class {},
    Raycaster: class {},
    Vector2: class {},
    Vector3: class {},
    Box3: class {},
  }
})

function jsonResponse(data: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 404,
    json: vi.fn().mockResolvedValue(data),
  }
}

function binaryResponse(ok = true, status = ok ? 200 : 404) {
  return {
    ok,
    status,
    headers: { get: vi.fn().mockReturnValue('model/gltf-binary') },
    arrayBuffer: vi.fn().mockResolvedValue(new ArrayBuffer(0)),
  }
}

function encodedBuffer(value: string): ArrayBuffer {
  return new TextEncoder().encode(value).buffer as ArrayBuffer
}

describe('model artifact format detection', () => {
  it('uses the response content type for STL and glTF artifacts', () => {
    expect(detectModelArtifactFormat('model/stl', new ArrayBuffer(0))).toBe('stl')
    expect(detectModelArtifactFormat('model/gltf-binary', new ArrayBuffer(0))).toBe('gltf')
  })

  it('sniffs legacy octet-stream responses without assuming glTF', () => {
    expect(detectModelArtifactFormat('application/octet-stream', encodedBuffer('solid exported\nendsolid exported'))).toBe('stl')
    expect(detectModelArtifactFormat('application/octet-stream', encodedBuffer('  {"asset":{"version":"2.0"}}'))).toBe('gltf')
    expect(detectModelArtifactFormat('application/octet-stream', encodedBuffer('glTF'))).toBe('gltf')
  })
})

describe('ViewerTab active state', () => {
  const originalRequestAnimationFrame = window.requestAnimationFrame
  const originalCancelAnimationFrame = window.cancelAnimationFrame

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(640)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(480)
    window.requestAnimationFrame = vi.fn(() => 1)
    window.cancelAnimationFrame = vi.fn()
    mocks.apiFetch
      .mockResolvedValueOnce(jsonResponse({ project_name: 'default_purlin' }))
      .mockResolvedValueOnce(jsonResponse({}, false))
  })

  it('uses stable green, red, and grey structural status colours', () => {
    expect(structuralCheckColor('pass')).toBe(0x22c55e)
    expect(structuralCheckColor('fail')).toBe(0xef4444)
    expect(structuralCheckColor('not_checked')).toBe(0x94a3b8)
  })

  it('distinguishes restraint state from missing physical evidence', () => {
    expect(structuralRestraintColor('verified')).toBe(0x22c55e)
    expect(structuralRestraintColor('candidate')).toBe(0xf59e0b)
    expect(structuralEvidenceColor('verified')).toBe(0x22c55e)
    expect(structuralEvidenceColor('missing')).toBe(0xef4444)
    expect(structuralEvidenceColor('mismatch')).toBe(0xef4444)
    expect(structuralEvidenceColor('not_checked')).toBe(0x94a3b8)
  })

  it('explains the selected structural stage in the viewer HUD', () => {
    render(
      <ModelViewerCanvas
        modelUrl=""
        getAccessToken={mocks.getAccessToken}
        statusText="Stage 8 Bracing/restraint"
        structuralOverlays={[{
          id: 'stage-focus-bracing',
          label: 'Stage 8 restraint focus',
          mode: 'moment',
          status: 'not_checked',
          stations: [],
          stageFocus: {
            id: 'bracing',
            order: 8,
            label: 'Bracing/restraint',
            status: 'warning',
            summary: 'One exact product candidate still lacks stiffness and anchorage.',
            visualDescription: 'Compression-flange restraint and physical evidence.',
            combinationLabel: 'ULS-WX+ · transverse wind',
            metrics: [{ label: 'Maximum required', value: '0.268 kN' }],
            legend: [
              { label: 'Exact-product candidate', tone: 'candidate' },
              { label: 'Missing stiffness / anchorage ring', tone: 'missing' },
            ],
          },
        }]}
      />,
    )

    expect(screen.getByText('Stage 8 visual check')).toBeInTheDocument()
    expect(screen.getByText('Bracing/restraint')).toBeInTheDocument()
    expect(screen.getByText('Compression-flange restraint and physical evidence.')).toBeInTheDocument()
    expect(screen.getByText('0.268 kN')).toBeInTheDocument()
    expect(screen.getByText('Missing stiffness / anchorage ring')).toBeInTheDocument()
  })

  it('renders viewer status and delegates toolbar controls through props', () => {
    const onFit = vi.fn()
    const onToggleRenderQuality = vi.fn()
    const onToggleGrid = vi.fn()
    const onToggleAutoRotate = vi.fn()

    render(
      <ViewerControls
        projectName="demo"
        renderQuality="high"
        showGrid
        autoRotate={false}
        loadErrorText={null}
        isModelLoading={false}
        statusText="Model ready"
        onFit={onFit}
        onToggleRenderQuality={onToggleRenderQuality}
        onToggleGrid={onToggleGrid}
        onToggleAutoRotate={onToggleAutoRotate}
      />,
    )

    expect(screen.getByText('demo')).toBeInTheDocument()
    expect(screen.getByText('Model ready')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Frame the whole model' }))
    fireEvent.click(screen.getByRole('button', { name: 'Visuals: High' }))
    fireEvent.click(screen.getByRole('button', { name: 'Grid: ON' }))
    fireEvent.click(screen.getByRole('button', { name: 'Rotate: OFF' }))

    expect(onFit).toHaveBeenCalledOnce()
    expect(onToggleRenderQuality).toHaveBeenCalledOnce()
    expect(onToggleGrid).toHaveBeenCalledOnce()
    expect(onToggleAutoRotate).toHaveBeenCalledOnce()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.requestAnimationFrame = originalRequestAnimationFrame
    window.cancelAnimationFrame = originalCancelAnimationFrame
  })

  it('does not create a renderer until the hidden viewport becomes active', async () => {
    const { rerender } = render(<ViewerTab serverUrl="/api/extus" isActive={false} />)

    expect(mocks.rendererSetSize).not.toHaveBeenCalled()
    expect(window.requestAnimationFrame).not.toHaveBeenCalled()

    rerender(<ViewerTab serverUrl="/api/extus" isActive />)
    await act(async () => {})

    expect(mocks.rendererSetSize).toHaveBeenLastCalledWith(640, 480)
    expect(mocks.rendererSetSize).toHaveBeenCalled()
  })

  it('does not fetch or parse a model while the canvas is inactive', async () => {
    mocks.apiFetch.mockReset()

    render(
      <ModelViewerCanvas
        modelUrl="/api/extus/artifacts/artifact-1/model"
        getAccessToken={mocks.getAccessToken}
        statusText="Selected historical model"
        isActive={false}
      />,
    )

    await act(async () => {})

    expect(mocks.apiFetch).not.toHaveBeenCalled()
    expect(mocks.gltfParse).not.toHaveBeenCalled()
  })

  it('shows a model load error and does not parse failed artifact responses', async () => {
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockResolvedValue(binaryResponse(false, 404))

    render(
      <ModelViewerCanvas
        modelUrl="/api/extus/artifacts/missing/model"
        getAccessToken={mocks.getAccessToken}
        statusText="Selected historical model"
      />,
    )

    expect(await screen.findByText('Model artifact unavailable (404)')).toBeInTheDocument()
    await waitFor(() => {
      expect(mocks.gltfParse).not.toHaveBeenCalled()
    })
  })

  it('shows loading text while a model artifact is being fetched', async () => {
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockReturnValue(new Promise(() => {}))

    render(
      <ModelViewerCanvas
        modelUrl="/api/extus/artifacts/artifact-1/model"
        getAccessToken={mocks.getAccessToken}
        statusText="Selected historical model"
      />,
    )

    expect(await screen.findByText('Loading model...')).toBeInTheDocument()
  })
})
