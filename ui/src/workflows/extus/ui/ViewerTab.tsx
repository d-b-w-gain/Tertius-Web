import React, { useState, useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';

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
  const [autoRotate, setAutoRotate] = useState<boolean>(true);
  const [renderQuality, setRenderQuality] = useState<'high' | 'low'>('high');
  
  const [sceneGraph, setSceneGraph] = useState<THREE.Object3D | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isolatedNodeId, setIsolatedNodeId] = useState<string | null>(null);
  
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const autoRotateRef = useRef<boolean>(true);
  
  // THREE.js refs
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const meshRef = useRef<THREE.Object3D | null>(null);
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
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    rendererRef.current = renderer;

    const controls = new OrbitControls(camera, canvas);
    controlsRef.current = controls;
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.autoRotate = autoRotateRef.current;
    controls.autoRotateSpeed = 1.5;
    
    let resumeTimeout: ReturnType<typeof setTimeout> | null = null;
    const handleInteraction = () => {
      controls.autoRotate = false;
      if (resumeTimeout) clearTimeout(resumeTimeout);
      resumeTimeout = setTimeout(() => {
        controls.autoRotate = autoRotateRef.current;
      }, 5000);
    };

    controls.addEventListener('start', handleInteraction);
    canvas.addEventListener('mousedown', handleInteraction);
    canvas.addEventListener('wheel', handleInteraction);
    
    // Lighting setup
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
    ambientLight.name = 'Ambient';
    scene.add(ambientLight);
    
    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.6);
    hemiLight.name = 'Hemi';
    hemiLight.position.set(0, 0, 200);
    scene.add(hemiLight);

    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(100, 100, 200);
    sun.castShadow = true;
    sun.shadow.mapSize.width = 2048;
    sun.shadow.mapSize.height = 2048;
    sun.shadow.bias = -0.0005;
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
      controls.removeEventListener('start', handleInteraction);
      canvas.removeEventListener('mousedown', handleInteraction);
      canvas.removeEventListener('wheel', handleInteraction);
      if (resumeTimeout) clearTimeout(resumeTimeout);
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

  useEffect(() => {
    autoRotateRef.current = autoRotate;
    if (controlsRef.current) {
       controlsRef.current.autoRotate = autoRotate;
    }
  }, [autoRotate]);

  useEffect(() => {
    if (!rendererRef.current || !sceneRef.current) return;
    const isHigh = renderQuality === 'high';
    
    rendererRef.current.shadowMap.enabled = isHigh;
    rendererRef.current.setPixelRatio(isHigh ? Math.min(window.devicePixelRatio, 2) : 1);
    
    sceneRef.current.traverse((node) => {
      if ((node as THREE.Light).isLight) {
         node.castShadow = isHigh && node.name !== 'Ambient' && node.name !== 'Hemi';
      }
      if ((node as THREE.Mesh).isMesh) {
         node.castShadow = isHigh;
         node.receiveShadow = isHigh;
         if ((node as THREE.Mesh).material) {
            ((node as THREE.Mesh).material as THREE.Material).needsUpdate = true;
         }
      }
    });
  }, [renderQuality]);

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
          if (mounted) setStatusText('No active model artifact found yet. Compile a project in Intus!');
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

  // 3. Load GLTF when URL changes
  useEffect(() => {
    if (!url || !sceneRef.current) return;
    
    let isCancelled = false;
    const loader = new GLTFLoader();
    
    apiFetch(url, getAccessToken)
      .then(res => res.arrayBuffer())
      .then(buffer => {
        if (isCancelled) return;
        loader.parse(buffer, '', (gltf) => {
          if (isCancelled) return;
      
          const model = gltf.scene;
      
      // Compute bounding box and center
      const box = new THREE.Box3().setFromObject(model);
      const center = new THREE.Vector3();
      box.getCenter(center);
      model.position.sub(center);
      
      // Fix orientation (GLTF is Y-up, our grid was Z-up)
      model.rotation.x = Math.PI / 2;
      
      // Update camera
      if (cameraRef.current) {
         const camera = cameraRef.current;
         const sphere = box.getBoundingSphere(new THREE.Sphere());
         const fov = camera.fov * (Math.PI / 180);
         let distance = Math.abs(sphere.radius / Math.sin(fov / 2));
         distance *= 1.5; // Padding
         
         const currentDir = new THREE.Vector3().subVectors(camera.position, new THREE.Vector3(0,0,0)).normalize();
         if (currentDir.lengthSq() === 0) currentDir.set(1, 1, 1).normalize();
         
         camera.position.copy(currentDir.multiplyScalar(distance));
         camera.lookAt(0, 0, 0);
         camera.updateProjectionMatrix();
         
         // Update helpers
         const size = Math.max(500, Math.ceil(sphere.radius * 4));
         const grid = sceneRef.current!.getObjectByName("GridHelper");
         if (grid) {
           const scale = size / 500;
           grid.scale.set(scale, scale, scale);
         }
         const axes = sceneRef.current!.getObjectByName("AxesHelper");
         if (axes) {
           const scale = size / 200;
           axes.scale.set(scale, scale, scale);
         }
      }
      
      // Override materials to add shadows and default color
      const sharedMaterial = new THREE.MeshStandardMaterial({
        color: 0x8b9bb4, // Steel blueish
        metalness: 0.6,
        roughness: 0.4,
        side: THREE.DoubleSide
      });
      
      const isHigh = renderQuality === 'high';
      model.traverse((child) => {
        if ((child as THREE.Mesh).isMesh) {
           const mesh = child as THREE.Mesh;
           mesh.castShadow = isHigh;
           mesh.receiveShadow = isHigh;
           mesh.material = sharedMaterial.clone(); // clone so we can set emissive independently
        }
      });
      
      if (meshRef.current) {
        sceneRef.current!.remove(meshRef.current);
      }
      
      sceneRef.current!.add(model);
      meshRef.current = model;
      
      // Unpack the hierarchy
      setSceneGraph(model);
      setSelectedNodeId(null);
      setIsolatedNodeId(null);
        }, (err) => {
          if (!isCancelled) console.error("Error parsing GLTF:", err);
        });
      })
      .catch(err => {
        if (!isCancelled) console.error("Error fetching GLTF:", err);
      });
      
    return () => {
      isCancelled = true;
    };
  }, [url, getAccessToken, renderQuality]);

  // 4. Handle Raycasting Interactions
  useEffect(() => {
    if (!canvasRef.current || !sceneRef.current || !cameraRef.current) return;
    const canvas = canvasRef.current;
    
    let clickTimeout: ReturnType<typeof setTimeout> | null = null;
    let clickCount = 0;
    
    const onMouseClick = (e: MouseEvent) => {
       const rect = canvas.getBoundingClientRect();
       const mouse = new THREE.Vector2(
         ((e.clientX - rect.left) / rect.width) * 2 - 1,
         -((e.clientY - rect.top) / rect.height) * 2 + 1
       );
       
       const raycaster = new THREE.Raycaster();
       raycaster.setFromCamera(mouse, cameraRef.current!);
       
       if (meshRef.current) {
          const intersects = raycaster.intersectObject(meshRef.current, true);
          if (intersects.length > 0) {
             let node: THREE.Object3D | null = intersects[0].object;
             const rootScene = meshRef.current.children[0]; // gltf.scene
             const assemblyRoot = rootScene && rootScene.children.length === 1 ? rootScene.children[0] : rootScene;

             // Walk up until the node is a direct child of the assembly root
             while (node && node.parent && node.parent !== assemblyRoot && node.parent !== rootScene && node.parent !== meshRef.current) {
                node = node.parent;
             }
             
             clickCount++;
             if (clickCount === 1) {
                clickTimeout = setTimeout(() => {
                   handleSelectNode(node!, false);
                   clickCount = 0;
                }, 250);
             } else if (clickCount === 2) {
                clearTimeout(clickTimeout!);
                handleSelectNode(node!, true);
                clickCount = 0;
             }
          } else {
             handleSelectNode(null, false);
          }
       }
    };
    
    canvas.addEventListener('click', onMouseClick);
    const preventDefault = (e: Event) => e.preventDefault();
    canvas.addEventListener('dblclick', preventDefault);
    
    return () => {
       canvas.removeEventListener('click', onMouseClick);
       canvas.removeEventListener('dblclick', preventDefault);
    };
  }, []);

  const handleSelectNode = (node: THREE.Object3D | null, isDouble: boolean) => {
     if (!node) {
        setSelectedNodeId(null);
        setIsolatedNodeId(null);
        localStorage.removeItem('tertius_selected_node');
        window.dispatchEvent(new Event('storage'));
        return;
     }
     
     const nodeName = node.name || '';
     localStorage.setItem('tertius_selected_node', nodeName);
     window.dispatchEvent(new Event('storage'));
     
     if (isDouble) {
        // Toggle isolation
        setIsolatedNodeId(prev => prev === node.uuid ? null : node.uuid);
        setSelectedNodeId(node.uuid);
     } else {
        setSelectedNodeId(node.uuid);
     }
  };

  useEffect(() => {
    const handleStorage = () => {
      const selectedName = localStorage.getItem('tertius_selected_node');
      if (!selectedName) {
         setSelectedNodeId(null);
         return;
      }
      
      if (meshRef.current) {
         const node = meshRef.current.getObjectByName(selectedName);
         if (node) {
            setSelectedNodeId(node.uuid);
         }
      }
    };
    
    handleStorage();
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, [sceneGraph]);


  // 5. Apply visibility and highlights
  useEffect(() => {
     if (!meshRef.current) return;
     
     // First, reset all visibility
     meshRef.current.traverse((child) => {
        child.visible = true;
     });
     
     // Apply Isolation
     if (isolatedNodeId) {
        meshRef.current.traverse((child) => {
           if (child !== meshRef.current) child.visible = false;
        });
        
        const isolated = meshRef.current.getObjectByProperty('uuid', isolatedNodeId);
        if (isolated) {
           // Make all parents visible so the path exists
           let p: THREE.Object3D | null = isolated;
           while (p && p !== meshRef.current) {
              p.visible = true;
              p = p.parent;
           }
           // Make all children visible
           isolated.traverse((c) => {
              c.visible = true;
           });
        }
     }
     
     // Apply Highlights
     meshRef.current.traverse((child) => {
        if ((child as THREE.Mesh).isMesh) {
           const mesh = child as THREE.Mesh;
           if (mesh.material && (mesh.material as THREE.MeshStandardMaterial).color) {
              const mat = mesh.material as THREE.MeshStandardMaterial;
              
              // Check if this mesh is under the selected node
              let p: THREE.Object3D | null = child;
              let isSelected = false;
              while (p && p !== meshRef.current) {
                 if (p.uuid === selectedNodeId) {
                    isSelected = true;
                    break;
                 }
                 p = p.parent;
              }
              
              if (isSelected) {
                 mat.emissive.setHex(0x3b82f6); // blue-500
                 mat.emissiveIntensity = 0.5;
              } else {
                 mat.emissive.setHex(0x000000);
                 mat.emissiveIntensity = 0;
              }
           }
        }
     });
     
  }, [selectedNodeId, isolatedNodeId, sceneGraph]);

  return (
    <div className="flex-1 relative bg-slate-900 flex overflow-hidden">
      
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
            onClick={() => setRenderQuality(renderQuality === 'high' ? 'low' : 'high')}
            className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${renderQuality === 'high' ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
          >
            Visuals: {renderQuality === 'high' ? 'High' : 'Low'}
          </button>
          <button 
            onClick={() => setShowGrid(!showGrid)}
            className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${showGrid ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
          >
            Grid: {showGrid ? 'ON' : 'OFF'}
          </button>
          <button 
            onClick={() => setAutoRotate(!autoRotate)}
            className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${autoRotate ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
          >
            Rotate: {autoRotate ? 'ON' : 'OFF'}
          </button>
        </div>
        <div className="text-xs text-slate-400">
          {statusText}
        </div>
      </div>
      
      {/* 3D Canvas */}
      <div className="flex-1 relative" ref={containerRef}>
        <canvas ref={canvasRef} className="w-full h-full block outline-none" />
      </div>
    </div>
  );
};
