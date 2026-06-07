import React, { useState, useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';

// Custom STL Parser to avoid missing STLLoader imports
const parseBinarySTL = (buffer: ArrayBuffer): { positions: number[]; normals: number[] } => {
  const reader = new DataView(buffer);
  const positions: number[] = [];
  const normals: number[] = [];
  const triangleCount = reader.getUint32(80, true);
  let offset = 84;
  for (let i = 0; i < triangleCount; i++) {
    const nx = reader.getFloat32(offset, true);
    const ny = reader.getFloat32(offset + 4, true);
    const nz = reader.getFloat32(offset + 8, true);
    offset += 12;
    for (let v = 0; v < 3; v++) {
      const x = reader.getFloat32(offset, true);
      const y = reader.getFloat32(offset + 4, true);
      const z = reader.getFloat32(offset + 8, true);
      offset += 12;
      positions.push(x, y, z);
      normals.push(nx, ny, nz);
    }
    offset += 2;
  }
  return { positions, normals };
};

const parseAsciiSTL = (text: string): { positions: number[]; normals: number[] } => {
  const positions: number[] = [];
  const normals: number[] = [];
  const lines = text.split('\n');
  let currentNormal = [0, 0, 1];
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('facet normal')) {
      const parts = trimmed.split(/\s+/);
      if (parts.length >= 5) {
        currentNormal = [parseFloat(parts[2]), parseFloat(parts[3]), parseFloat(parts[4])];
      }
    } else if (trimmed.startsWith('vertex')) {
      const parts = trimmed.split(/\s+/);
      if (parts.length >= 4) {
        positions.push(parseFloat(parts[1]), parseFloat(parts[2]), parseFloat(parts[3]));
        normals.push(...currentNormal);
      }
    }
  }
  return { positions, normals };
};

const parseSTL = (buffer: ArrayBuffer): { positions: number[]; normals: number[] } => {
  const reader = new DataView(buffer);
  if (buffer.byteLength > 84) {
    const triangleCount = reader.getUint32(80, true);
    const expectedBinarySize = 84 + triangleCount * 50;
    if (buffer.byteLength === expectedBinarySize) {
      return parseBinarySTL(buffer);
    }
  }
  const text = new TextDecoder().decode(buffer);
  if (text.trim().startsWith('solid') && text.includes('facet normal')) {
    return parseAsciiSTL(text);
  }
  return parseBinarySTL(buffer);
};

interface ViewerProps {
  serverUrl: string;
  isActive?: boolean;
}

export const ViewerTab: React.FC<ViewerProps> = ({ serverUrl, isActive = true }) => {
  const { getAccessToken } = useAuth();
  const [statusText, setStatusText] = useState('Waiting for connection...');
  const [url, setUrl] = useState<string>('');
  const [projectName, setProjectName] = useState<string>('');
  const [showGrid, setShowGrid] = useState<boolean>(true);
  
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  
  // THREE.js refs
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const meshRef = useRef<THREE.Mesh | null>(null);
  const animIdRef = useRef<number>(0);

  // 1. Initialize Scene (run once)
  useEffect(() => {
    if (!containerRef.current || !canvasRef.current) return;
    const container = containerRef.current;
    const canvas = canvasRef.current;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0f172a); // slate-900
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.1, 100000);
    camera.up.set(0, 0, 1);
    camera.position.set(200, 200, 200);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    rendererRef.current = renderer;

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 1.5;
    
    // Lighting setup
    scene.add(new THREE.AmbientLight(0xffffff, 0.4));
    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.6);
    hemiLight.position.set(0, 0, 200);
    scene.add(hemiLight);

    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(100, 100, 200);
    sun.castShadow = true;
    scene.add(sun);
    
    // Grid and Axes Helpers
    const gridHelper = new THREE.GridHelper(500, 50, 0x888888, 0x444444);
    gridHelper.rotation.x = Math.PI / 2; // Z-up orientation
    gridHelper.name = "GridHelper";
    scene.add(gridHelper);

    const axesHelper = new THREE.AxesHelper(100);
    axesHelper.name = "AxesHelper";
    scene.add(axesHelper);

    const onResize = () => {
      if (!container || !renderer || !camera) return;
      const w = container.clientWidth || 1;
      const h = container.clientHeight || 1;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', onResize);

    const animate = () => {
      animIdRef.current = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      window.removeEventListener('resize', onResize);
      cancelAnimationFrame(animIdRef.current);
      renderer.dispose();
    };
  }, []);

  useEffect(() => {
    if (!sceneRef.current) return;
    const grid = sceneRef.current.getObjectByName("GridHelper");
    const axes = sceneRef.current.getObjectByName("AxesHelper");
    if (grid) grid.visible = showGrid;
    if (axes) axes.visible = showGrid;
  }, [showGrid]);

  // 2. Poll for file changes
  useEffect(() => {
    if (!isActive) return;
    
    let mounted = true;
    let mtime = 0;
    
    const checkStatus = async () => {
      try {
        const projRes = await apiFetch(`${serverUrl}/project_name`, getAccessToken);
        if (projRes.ok && mounted) {
          const pData = await projRes.json();
          if (pData.project_name) {
            setProjectName(pData.project_name);
          }
        }
        
        const res = await apiFetch(`${serverUrl}/status`, getAccessToken);
        if (res.ok) {
          const data = await res.json();
          if (data.mtime && data.mtime !== mtime) {
            if (mounted) {
              mtime = data.mtime;
              setUrl(`${serverUrl}/model?t=${data.mtime}`);
              setStatusText(`Model updated at ${new Date(data.mtime * 1000).toLocaleTimeString()}`);
            }
          }
        } else {
          if (mounted) setStatusText('No active_output.stl found yet. Compile a project in Intus!');
        }
      } catch (e) {
        if (mounted) setStatusText('Lost connection to file server.');
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [serverUrl, isActive, getAccessToken]);

  // 3. Load STL when URL changes
  useEffect(() => {
    if (!url || !sceneRef.current) return;
    
    let isCancelled = false;

    apiFetch(url, getAccessToken)
      .then(res => res.arrayBuffer())
      .then(buffer => {
        if (isCancelled) return;
        try {
          const { positions, normals } = parseSTL(buffer);
          if (positions.length === 0) return;

          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
          geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
          
          geometry.computeVertexNormals();
          
          // Center the geometry
          geometry.computeBoundingBox();
          geometry.computeBoundingSphere();
          const sphere = geometry.boundingSphere;

          const center = new THREE.Vector3();
          geometry.boundingBox?.getCenter(center);
          geometry.translate(-center.x, -center.y, -center.z);
          if (sphere) sphere.center.set(0, 0, 0);

          if (cameraRef.current && sphere) {
            const camera = cameraRef.current;
            const fov = camera.fov * (Math.PI / 180);
            let distance = Math.abs(sphere.radius / Math.sin(fov / 2));
            distance *= 1.5; // Padding
            
            const currentDir = new THREE.Vector3().subVectors(camera.position, new THREE.Vector3(0,0,0)).normalize();
            if (currentDir.lengthSq() === 0) currentDir.set(1, 1, 1).normalize();
            
            camera.position.copy(currentDir.multiplyScalar(distance));
            camera.lookAt(0, 0, 0);
            camera.updateProjectionMatrix();
          }
          
          if (sphere && sceneRef.current) {
            const size = Math.max(500, Math.ceil(sphere.radius * 4));
            
            const grid = sceneRef.current.getObjectByName("GridHelper");
            if (grid) {
              const scale = size / 500;
              grid.scale.set(scale, scale, scale);
            }
            
            const axes = sceneRef.current.getObjectByName("AxesHelper");
            if (axes) {
              const scale = size / 200;
              axes.scale.set(scale, scale, scale);
            }
          }

          const material = new THREE.MeshStandardMaterial({
            color: 0x8b9bb4, // Steel blueish
            metalness: 0.6,
            roughness: 0.4,
            side: THREE.DoubleSide
          });

          const mesh = new THREE.Mesh(geometry, material);
          mesh.castShadow = true;
          mesh.receiveShadow = true;

          if (meshRef.current) {
            sceneRef.current!.remove(meshRef.current);
            meshRef.current.geometry.dispose();
            (meshRef.current.material as THREE.Material).dispose();
          }

          sceneRef.current!.add(mesh);
          meshRef.current = mesh;
        } catch (err) {
          console.error("Error parsing STL:", err);
        }
      })
      .catch(err => {
        if (!isCancelled) console.error("Error fetching STL:", err);
      });
      
    return () => {
      isCancelled = true;
    };
  }, [url, getAccessToken]);

  return (
    <div className="flex-1 relative bg-slate-900" ref={containerRef}>
      {/* Overlay Status */}
      <div className="absolute top-4 left-4 z-10 bg-slate-950/80 backdrop-blur border border-slate-800 rounded-lg p-3 shadow-xl pointer-events-none flex flex-col gap-2">
        <div className="flex items-center justify-between gap-4">
          <div className="text-xs font-mono font-medium text-sky-400 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-sky-500 animate-pulse" />
            Extus Viewer
          </div>
          {projectName && (
            <div className="text-xs font-bold text-slate-300 bg-slate-800 px-2 py-0.5 rounded border border-slate-700">
              {projectName}
            </div>
          )}
          <button 
            onClick={() => setShowGrid(!showGrid)}
            className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${showGrid ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
          >
            Grid: {showGrid ? 'ON' : 'OFF'}
          </button>
        </div>
        <div className="text-xs text-slate-400">
          {statusText}
        </div>
      </div>
      
      {/* 3D Canvas */}
      <canvas ref={canvasRef} className="w-full h-full block" />
    </div>
  );
};
