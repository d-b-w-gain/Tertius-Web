import React, { useState, useEffect, useRef } from 'react';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

export const DraftingTab: React.FC<{ serverUrl: string, isActive?: boolean }> = ({ serverUrl, isActive = true }) => {
  const { getAccessToken } = useAuth();
  const [activeProject, setActiveProject] = useState<string>('');
  
  // Customizer State
  const [title, setTitle] = useState('UNTITLED PART');
  const [stampText, setStampText] = useState('APPROVED');
  const [showRedline, setShowRedline] = useState(true);
  const [showHiddenLines, setShowHiddenLines] = useState(false);
  const [scale, setScale] = useState(1.0);
  const [sheetSize, setSheetSize] = useState('A4');
  const [selectedView, setSelectedView] = useState('combined');
  
  const [debouncedScale, setDebouncedScale] = useState(1.0);
  const [debouncedTitle, setDebouncedTitle] = useState('UNTITLED PART');
  
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [buildStatus, setBuildStatus] = useState<string>('none');

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedScale(scale), 300);
    return () => clearTimeout(timer);
  }, [scale]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedTitle(title), 500);
    return () => clearTimeout(timer);
  }, [title]);

  useEffect(() => {
    let isMounted = true;
    const fetchActive = async () => {
      try {
        const res = await apiFetch(`${serverUrl}/project_name`, getAccessToken);
        if (res.ok && isMounted) {
           const data = await res.json();
           if (data.project_name) setActiveProject(data.project_name);
        }
      } catch (e) {
        console.error("Failed to fetch active project");
      }
    };
    
    fetchActive();
    const interval = setInterval(fetchActive, 2000);
    return () => {
        isMounted = false;
        clearInterval(interval);
    };
  }, [serverUrl, getAccessToken]);

  useEffect(() => {
    if (!activeProject) return;
    let isMounted = true;

    const loadSettings = async () => {
      setSettingsLoaded(false);
      setTitle(activeProject.toUpperCase());
      setStampText('APPROVED');
      setShowRedline(true);
      setShowHiddenLines(false);
      setScale(1.0);
      setSheetSize('A4');

      try {
        const res = await apiFetch(`${serverUrl}/projects/${activeProject}/settings`, getAccessToken);
        if (!res.ok || !isMounted) return;
        const parsed = await res.json();
        if (parsed.title) setTitle(parsed.title);
        if (parsed.stamp_text) setStampText(parsed.stamp_text);
        if (parsed.show_redline !== undefined) setShowRedline(parsed.show_redline);
        if (parsed.show_hidden_lines !== undefined) setShowHiddenLines(parsed.show_hidden_lines);
        if (parsed.scale) setScale(parsed.scale);
        if (parsed.sheet_size) setSheetSize(parsed.sheet_size);
      } catch (e) {
        console.error("Failed to load Timus settings");
      } finally {
        if (isMounted) setSettingsLoaded(true);
      }
    };

    loadSettings();
    return () => {
      isMounted = false;
    };
  }, [activeProject, serverUrl, getAccessToken]);

  useEffect(() => {
    if (!activeProject || !settingsLoaded) return;
    const settings = {
      title,
      stamp_text: stampText,
      show_redline: showRedline,
      show_hidden_lines: showHiddenLines,
      scale,
      sheet_size: sheetSize
    };
    apiFetch(`${serverUrl}/projects/${activeProject}/settings`, getAccessToken, {
      method: 'PUT',
      body: JSON.stringify(settings),
    }).catch(() => {
      console.error("Failed to save Timus settings");
    });
  }, [activeProject, settingsLoaded, title, stampText, showRedline, showHiddenLines, scale, sheetSize, serverUrl, getAccessToken]);

  useEffect(() => {
    if (!activeProject || !isActive) return;
    let mounted = true;
    const checkStatus = async () => {
      try {
        const res = await apiFetch(`${serverUrl}/projects/${activeProject}/drafting/status`, getAccessToken);
        if (res.ok && mounted) {
          const data = await res.json();
          setBuildStatus(data.status);
        }
      } catch (e) {}
    };
    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => { mounted = false; clearInterval(interval); };
  }, [activeProject, isActive, serverUrl, getAccessToken]);

  const triggerBuild = async () => {
    if (!activeProject) return;
    setBuildStatus('building');
    try {
      await apiFetch(`${serverUrl}/projects/${activeProject}/drafting/build`, getAccessToken, { method: 'POST' });
    } catch (e) {
      console.error(e);
    }
  };

  const handleDownloadPdf = async () => {
    if (!activeProject || buildStatus !== 'ready') return;
    try {
      const url = `${serverUrl}/projects/${activeProject}/drafting.pdf?title=${encodeURIComponent(debouncedTitle)}&stamp=${encodeURIComponent(stampText)}&redline=${showRedline}&hidden_lines=${showHiddenLines}&scale=${debouncedScale}&size=${sheetSize}`;
      const res = await apiFetch(url, getAccessToken);
      if (!res.ok) return;
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      window.open(objectUrl, '_blank', 'noopener,noreferrer');
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
    } catch (e) {
      console.error("Failed to download PDF:", e);
    }
  };

  if (!activeProject) {
    return (
      <div className="flex-1 flex justify-center items-center bg-slate-900 text-slate-500 font-mono text-sm">
        No active project found. Compile a project in Intus first.
      </div>
    );
  }

  return (
    <div className="flex-1 flex min-h-0 overflow-hidden bg-slate-900 selection:bg-cyan-500/30">
      <div className="w-80 border-r border-slate-800 bg-slate-950 p-6 flex flex-col justify-between overflow-y-auto">
        <div className="space-y-6">
          <div>
            <h3 className="text-lg font-bold text-orange-400">Timus Compiler</h3>
            <p className="text-xs text-slate-500">Automated A4 Drafting-Sheet PDF Generation.</p>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Sheet Size</label>
            <select
              value={sheetSize}
              onChange={(e) => setSheetSize(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
            >
              <option value="A4">A4 (297 × 210 mm)</option>
              <option value="A3">A3 (420 × 297 mm)</option>
              <option value="A2">A2 (594 × 420 mm)</option>
              <option value="A1">A1 (841 × 594 mm)</option>
              <option value="A0">A0 (1189 × 841 mm)</option>
            </select>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Layout</label>
            <select
              value={selectedView}
              onChange={(e) => setSelectedView(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
            >
              <option value="combined">Combined (4 Views)</option>
              <option value="top">Top View Only</option>
              <option value="front">Front Elevation Only</option>
              <option value="side">Side Elevation Only</option>
              <option value="iso">Isometric View Only</option>
            </select>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Drawing Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Projection Scale</label>
            <select
              value={scale.toString()}
              onChange={(e) => setScale(parseFloat(e.target.value))}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
            >
              <option value="10">10:1 (Enlarged 10x)</option>
              <option value="5">5:1 (Enlarged 5x)</option>
              <option value="2">2:1 (Enlarged 2x)</option>
              <option value="1">1:1 (Full Size)</option>
              <option value="0.5">1:2 (Half Size)</option>
              <option value="0.2">1:5</option>
              <option value="0.1">1:10</option>
              <option value="0.05">1:20</option>
              <option value="0.02">1:50</option>
              <option value="0.01">1:100</option>
              <option value="0.005">1:200</option>
              <option value="0.002">1:500</option>
              <option value="0.001">1:1000</option>
            </select>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-slate-400">Redline Stamp Code</label>
            <input
              type="text"
              value={stampText}
              onChange={(e) => setStampText(e.target.value.substring(0, 16).toUpperCase())}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-orange-400"
              placeholder="e.g. APPROVED"
            />
          </div>

          <div className="flex items-center justify-between border-t border-slate-900 pt-4">
            <span className="text-xs font-mono uppercase tracking-wider text-slate-400">Show Redline Markups</span>
            <button
              onClick={() => setShowRedline(!showRedline)}
              className={`relative inline-flex h-5 w-10 items-center rounded-full transition-colors outline-none ${showRedline ? 'bg-orange-500' : 'bg-slate-800'}`}
            >
              <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${showRedline ? 'translate-x-5.5' : 'translate-x-1'}`} />
            </button>
          </div>

          <div className="flex items-center justify-between pt-1">
            <span className="text-xs font-mono uppercase tracking-wider text-slate-400">Show Hidden Lines</span>
            <button
              onClick={() => setShowHiddenLines(!showHiddenLines)}
              className={`relative inline-flex h-5 w-10 items-center rounded-full transition-colors outline-none ${showHiddenLines ? 'bg-orange-500' : 'bg-slate-800'}`}
            >
              <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${showHiddenLines ? 'translate-x-5.5' : 'translate-x-1'}`} />
            </button>
          </div>
        </div>

        <div className="space-y-2 pt-6 border-t border-slate-900">
          <button
            onClick={triggerBuild}
            disabled={buildStatus === 'building'}
            className={`w-full font-semibold py-2 px-4 rounded border transition-all text-xs flex justify-center items-center gap-1.5 cursor-pointer ${
              buildStatus === 'building' 
                ? 'bg-orange-900/50 border-orange-800 text-orange-400 opacity-70 cursor-not-allowed' 
                : 'bg-slate-900 hover:bg-slate-850 text-slate-300 hover:text-slate-100 border-slate-800'
            }`}
          >
            {buildStatus === 'building' ? '⚙️ Calculating PDF Lines...' : '🔄 Generate PDF Data'}
          </button>
          <button
            type="button"
            onClick={handleDownloadPdf}
            disabled={buildStatus !== 'ready'}
            className={`w-full font-bold py-2.5 px-4 rounded transition-all text-xs flex justify-center items-center gap-1.5 shadow-lg ${
              buildStatus === 'ready'
                ? 'bg-orange-600 hover:bg-orange-500 text-white shadow-orange-500/20 cursor-pointer'
                : 'bg-slate-800 text-slate-500 shadow-none cursor-not-allowed'
            }`}
          >
            📥 Download PDF
          </button>
          {buildStatus === 'stale' && (
             <div className="text-[10px] text-orange-400 text-center font-mono uppercase mt-1">
               Project modified. Regenerate PDF Data.
             </div>
          )}
        </div>
      </div>

      <div className="flex-1 p-6 overflow-hidden flex flex-col items-center space-y-4 select-none bg-slate-800">
        <div className="text-center space-y-1 z-20">
          <h2 className="text-xl font-bold text-white tracking-wide">Timus Interactive Preview</h2>
          <p className="text-xs text-slate-400">Instant WebGL approximation. Export for perfect vectors.</p>
        </div>

        <div className="flex-1 w-full max-w-5xl bg-slate-950 shadow-2xl overflow-hidden rounded relative flex items-center justify-center p-4">
          <DraftingCanvas 
            sheetSize={sheetSize} 
            title={debouncedTitle} 
            stampText={stampText} 
            showRedline={showRedline} 
            showHiddenLines={showHiddenLines} 
            scale={debouncedScale} 
            serverUrl={serverUrl}
            activeProject={activeProject}
            getAccessToken={getAccessToken}
            isActive={isActive}
            selectedView={selectedView}
          />
        </div>
      </div>
    </div>
  );
};

const DraftingCanvas: React.FC<{
  sheetSize: string;
  title: string;
  stampText: string;
  showRedline: boolean;
  showHiddenLines: boolean;
  scale: number;
  serverUrl: string;
  activeProject: string;
  getAccessToken: () => Promise<string>;
  isActive: boolean;
  selectedView: string;
}> = ({ sheetSize, title, stampText, showRedline, scale, serverUrl, activeProject, getAccessToken, isActive, selectedView }) => {
  const formats: Record<string, [number, number]> = {
    "A4": [297, 210], "A3": [420, 297], "A2": [594, 420], "A1": [841, 594], "A0": [1189, 841]
  };
  const [w, h] = formats[sheetSize] || [297, 210];
  const view_w = (w - 60) / 2;
  const view_h = (h - 60) / 2;

  const svgRef = useRef<SVGSVGElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  
  const [modelUrl, setModelUrl] = useState<string>('');
  
  // Model Polling
  useEffect(() => {
    if (!activeProject || !isActive) return;
    let mounted = true;
    let mtime = 0;
    const checkModel = async () => {
      try {
        const res = await apiFetch(`${serverUrl}/projects/${activeProject}/model_status`, getAccessToken);
        if (res.ok && mounted) {
          const data = await res.json();
          if (data.mtime && data.mtime !== mtime) {
            mtime = data.mtime;
            setModelUrl(`${serverUrl}/projects/${activeProject}/model?t=${mtime}`);
          }
        }
      } catch (e) {}
    };
    checkModel();
    const interval = setInterval(checkModel, 3000);
    return () => { mounted = false; clearInterval(interval); };
  }, [serverUrl, activeProject, isActive, getAccessToken]);

  const stateRef = useRef({ w, h, view_w, view_h, scale, selectedView });
  useEffect(() => {
    stateRef.current = { w, h, view_w, view_h, scale, selectedView };
  }, [w, h, view_w, view_h, scale, selectedView]);

  // Three.js renderer (Only initialize ONCE per model)
  useEffect(() => {
    if (!modelUrl) return;
    const canvas = canvasRef.current;
    const svg = svgRef.current;
    if (!canvas || !svg) return;

    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setClearColor(0xffffff, 0);

    const scene = new THREE.Scene();
    
    let isCancelled = false;
    const loader = new GLTFLoader();
    
    apiFetch(modelUrl, getAccessToken)
      .then(res => res.arrayBuffer())
      .then(buffer => {
        if (isCancelled) return;
        loader.parse(buffer, '', (gltf) => {
          if (isCancelled) return;
          const model = gltf.scene;
          
          const box = new THREE.Box3().setFromObject(model);
          const center = new THREE.Vector3();
          box.getCenter(center);
          model.position.sub(center);
          model.rotation.x = Math.PI / 2;

          const solidMat = new THREE.MeshBasicMaterial({ 
            color: 0xffffff, 
            polygonOffset: true, 
            polygonOffsetFactor: 1, 
            polygonOffsetUnits: 1 
          });
          const lineMat = new THREE.LineBasicMaterial({ color: 0x64748b, transparent: true, opacity: 0.7 });

          model.traverse((child) => {
            if ((child as THREE.Mesh).isMesh) {
               const mesh = child as THREE.Mesh;
               mesh.material = solidMat;
               const edges = new THREE.EdgesGeometry(mesh.geometry, 30);
               const line = new THREE.LineSegments(edges, lineMat);
               mesh.add(line);
            }
          });
          
          scene.add(model);
          draw(); // Force a redraw now that the model is loaded!
        });
      });

    const dpr = window.devicePixelRatio;
    
    const topCam = new THREE.OrthographicCamera();
    const frontCam = new THREE.OrthographicCamera();
    const sideCam = new THREE.OrthographicCamera();
    const isoCam = new THREE.OrthographicCamera();
    
    topCam.position.set(0, 0, 500); topCam.lookAt(0, 0, 0);
    frontCam.position.set(0, -500, 0); frontCam.up.set(0, 0, 1); frontCam.lookAt(0, 0, 0);
    sideCam.position.set(500, 0, 0); sideCam.up.set(0, 0, 1); sideCam.lookAt(0, 0, 0);
    isoCam.position.set(500, -500, 500); isoCam.up.set(0, 0, 1); isoCam.lookAt(0, 0, 0);
    
    const updateCameras = (w_px: number, h_px: number, projW: number, projH: number) => {
        const s = stateRef.current;
        const factor = Math.min(w_px / projW, h_px / projH);
        const camW = (w_px / factor / s.scale) / 1000;
        const camH = (h_px / factor / s.scale) / 1000;
        
        [topCam, frontCam, sideCam, isoCam].forEach(cam => {
            const newLeft = -camW / 2;
            const newTop = camH / 2;
            if (cam.left !== newLeft || cam.top !== newTop) {
                cam.left = newLeft;
                cam.right = camW / 2;
                cam.top = newTop;
                cam.bottom = -camH / 2;
                cam.updateProjectionMatrix();
            }
        });
    };

    const draw = () => {
      if (isCancelled) return;
      const rect = svg.getBoundingClientRect();
      if (rect.width === 0) return;
      const svgElement = svg as unknown as HTMLElement;
      
      const width = rect.width * dpr;
      const height = rect.height * dpr;
      canvas.style.top = `${svgElement.offsetTop}px`;
      canvas.style.left = `${svgElement.offsetLeft}px`;
      if (canvas.width !== width || canvas.height !== height) {
          renderer.setSize(rect.width, rect.height, false);
          canvas.style.width = `${rect.width}px`;
          canvas.style.height = `${rect.height}px`;
      }
      
      renderer.clear();
      
      const s = stateRef.current;
      const pxPerMm = rect.width / s.w;
      
      const renderView = (cam: THREE.Camera, oxMm: number, oyMm: number, wMm: number, hMm: number) => {
          const vX = oxMm * pxPerMm;
          const vY = rect.height - (oyMm + hMm) * pxPerMm;
          const vW = wMm * pxPerMm;
          const vH = hMm * pxPerMm;
          
          renderer.setViewport(vX, vY, vW, vH);
          renderer.setScissor(vX, vY, vW, vH);
          renderer.setScissorTest(true);
          updateCameras(vW * dpr, vH * dpr, wMm, hMm);
          renderer.render(scene, cam);
      };
      
      if (s.selectedView === 'combined' || s.selectedView === 'top') renderView(topCam, 20, 30, s.view_w, s.view_h);
      if (s.selectedView === 'combined' || s.selectedView === 'front') renderView(frontCam, 20, 30 + s.view_h, s.view_w, s.view_h);
      if (s.selectedView === 'combined' || s.selectedView === 'side') renderView(sideCam, 40 + s.view_w, 30 + s.view_h, s.view_w, s.view_h);
      if (s.selectedView === 'combined' || s.selectedView === 'iso') renderView(isoCam, 40 + s.view_w, 30, s.view_w, s.view_h);
    };
    
    // Draw once immediately
    draw();
    
    // Draw again whenever scale/settings change (via stateRef)
    // We only want to draw if something ACTUALLY changed to save CPU.
    let lastStateStr = JSON.stringify(stateRef.current);
    const redrawInterval = setInterval(() => {
        const currentStateStr = JSON.stringify(stateRef.current);
        if (currentStateStr !== lastStateStr) {
            lastStateStr = currentStateStr;
            draw();
        }
    }, 200);
    
    let resizeTimeout: any;
    const ro = new ResizeObserver(() => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(draw, 50);
    });
    ro.observe(svg);
    
    return () => {
        isCancelled = true;
        clearInterval(redrawInterval);
        clearTimeout(resizeTimeout);
        ro.disconnect();
        scene.traverse((child) => {
          if ((child as THREE.Mesh).isMesh) {
            const m = child as THREE.Mesh;
            m.geometry.dispose();
            if (m.material) {
              if (Array.isArray(m.material)) m.material.forEach(mat => mat.dispose());
              else m.material.dispose();
            }
          } else if ((child as THREE.LineSegments).isLineSegments) {
            const l = child as THREE.LineSegments;
            l.geometry.dispose();
            if (l.material) {
              if (Array.isArray(l.material)) l.material.forEach(mat => mat.dispose());
              else l.material.dispose();
            }
          }
        });
        renderer.dispose();
    };
  }, [modelUrl, getAccessToken]);

  return (
    <div className="relative w-full h-full flex items-center justify-center">
      <svg ref={svgRef} viewBox={`0 0 ${w} ${h}`} className="max-w-full max-h-full drop-shadow-2xl bg-white border border-slate-700 z-10" style={{ position: 'relative' }}>
        {/* Background Grid - Faint Grid Paper effect */}
        <defs>
          <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse" x="10" y="10">
            <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#f1f5f9" strokeWidth="0.1"/>
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
        
        {/* Borders */}
        <rect x="10" y="10" width={w - 20} height={h - 20} fill="none" stroke="#0f172a" strokeWidth="0.55" />
        
        {/* Border coordinate ticks */}
        <g stroke="#0f172a" strokeWidth="0.18">
          {[1, 2, 3].map(i => {
            const x = 10 + i * ((w - 20) / 4);
            return (
              <g key={`x-${i}`}>
                <line x1={x} y1="10" x2={x} y2="7" />
                <line x1={x} y1={h - 10} x2={x} y2={h - 7} />
              </g>
            );
          })}
          {[1, 2, 3].map(i => {
            const y = 10 + i * ((h - 20) / 4);
            return (
              <g key={`y-${i}`}>
                <line x1="10" y1={y} x2="7" y2={y} />
                <line x1={w - 10} y1={y} x2={w - 7} y2={y} />
              </g>
            );
          })}
        </g>

        {/* Coordinate Labels */}
        <g fontFamily="monospace" fontWeight="bold" fontSize="3" fill="#4b5563">
          {["4", "3", "2", "1"].map((col, i) => {
            const x = 10 + (i + 0.5) * ((w - 20) / 4);
            return (
              <g key={`col-${i}`}>
                <text x={x - 0.8} y="8.5">{col}</text>
                <text x={x - 0.8} y={h - 3.5}>{col}</text>
              </g>
            );
          })}
          {["D", "C", "B", "A"].map((row, i) => {
            const y = 10 + (i + 0.5) * ((h - 20) / 4);
            return (
              <g key={`row-${i}`}>
                <text x="5.5" y={y + 1.2}>{row}</text>
                <text x={w - 5.5} y={y + 1.2}>{row}</text>
              </g>
            );
          })}
        </g>
        
        {/* Title Block */}
        <g transform={`translate(${w - 110}, ${h - 35})`}>
          {/* Borders */}
          <g stroke="#0f172a" strokeWidth="0.38">
            <rect x="0" y="0" width="100" height="25" fill="none" />
            <line x1="0" y1="9" x2="100" y2="9" />
            <line x1="0" y1="18" x2="100" y2="18" />
            <line x1="50" y1="0" x2="50" y2="18" />
            <line x1="80" y1="0" x2="80" y2="18" />
            <line x1="35" y1="18" x2="35" y2="25" />
          </g>

          {/* Row 1 */}
          <text x="2" y="3" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">DRAWING TITLE</text>
          <text x="2" y="7" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">{title}</text>
          
          <text x="52" y="3" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">DOCUMENT NO.</text>
          <text x="52" y="7" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">TERTIUS-DWG-001</text>
          
          <text x="82" y="3" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">SHEET NO.</text>
          <text x="82" y="7" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">1 OF 1</text>

          {/* Row 2 */}
          <text x="2" y="11" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">CHECKED BY</text>
          <text x="2" y="15" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">TERTIUS SYSTEMS ENG</text>
          
          <text x="52" y="11" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">REVISION STATUS</text>
          <text x="52" y="15" fontSize="3.5" fontFamily="sans-serif" fill="#0f172a">REV 1.0</text>
          
          <text x="82" y="11" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">SCALE</text>
          <text x="82" y="15" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">NTS</text>

          {/* Row 3 */}
          <text x="2" y="19" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">APPLICANT NAME</text>
          <text x="2" y="23" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">PLACEHOLDER NAME</text>
          
          <text x="37" y="19" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">SYSTEM</text>
          <text x="37" y="23" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">TERTIUS CAD COMPILER</text>

          {/* Stamp */}
          {showRedline && stampText && (
            <g>
              <line x1="52" y1="14.0" x2="63" y2="14.0" stroke="#ef4444" strokeWidth="0.3" />
              <text x="64" y="15" fontSize="4" fontFamily="sans-serif" fontWeight="bold" fill="#ef4444">{stampText}</text>
              <g transform="translate(40, 14) rotate(-3)">
                <rect x="-2" y="-5" width="10" height="4" fill="none" stroke="#ef4444" strokeWidth="0.3" />
                <text x="-1" y="-2" fontSize="2.5" fontFamily="sans-serif" fontWeight="bold" fill="#ef4444">QTD OK</text>
              </g>
            </g>
          )}
        </g>
        
        {/* Company Name */}
        <text x="15" y="25" fontSize="8" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">TERTIUS ENGINEERING</text>

        {/* View Grid Lines */}
        {/* View Labels */}
        <g fontSize="3" fontFamily="sans-serif" fontWeight="bold" fill="#9ca3af">
          <text x={20} y={30 + view_h - 2}>PLAN VIEW</text>
          <text x={20} y={h - 20}>FRONT ELEVATION</text>
          <text x={40 + view_w} y={h - 20}>SIDE ELEVATION</text>
          <text x={40 + view_w} y={30 + view_h - 2}>ISOMETRIC VIEW</text>
        </g>
      </svg>
      
      <canvas 
        ref={canvasRef} 
        style={{
          position: 'absolute',
          pointerEvents: 'none',
          zIndex: 20
        }} 
      />
    </div>
  );
};
