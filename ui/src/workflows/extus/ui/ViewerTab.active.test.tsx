import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ViewerTab } from './ViewerTab'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  rendererSetSize: vi.fn(),
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
    parse = vi.fn()
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
    json: vi.fn().mockResolvedValue(data),
  }
}

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

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.requestAnimationFrame = originalRequestAnimationFrame
    window.cancelAnimationFrame = originalCancelAnimationFrame
  })

  it('resizes the renderer when the hidden viewport becomes active', async () => {
    const { rerender } = render(<ViewerTab serverUrl="/api/extus" isActive={false} />)

    expect(mocks.rendererSetSize).toHaveBeenCalledTimes(1)

    rerender(<ViewerTab serverUrl="/api/extus" isActive />)
    await act(async () => {})

    expect(mocks.rendererSetSize).toHaveBeenLastCalledWith(640, 480)
    expect(mocks.rendererSetSize).toHaveBeenCalledTimes(2)
  })
})
