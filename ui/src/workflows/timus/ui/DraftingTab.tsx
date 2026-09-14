import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { GuestWorkflowNotice } from '../../shared/ui/GuestWorkflowNotice';
import {
  ACTIVE_PROJECT_POLL_INTERVAL_MS,
  MODEL_STATUS_POLL_INTERVAL_MS,
  getPollingDelay,
  shouldRunPollingRequest,
} from '../../shared/polling';
import { runWithInteractionSpan } from '../../../telemetry';
import {
  DraftingInspector,
  DraftingStatusBar,
  DraftingToolbar,
  DrawingSetNavigator,
  draftingScalePresets,
} from './DraftingWorkspaceChrome';
import {
  buildDraftingPdfUrl,
  formatDraftingScale,
  getDraftingViewLayout,
  type DraftingLayout,
  type DraftingViewName,
  type DraftingViewRect,
} from './draftingLayout';

const DEFAULT_ISSUE_STATUS = 'PRELIMINARY';
const NAVIGATOR_STORAGE_KEY = 'tertius.timus.navigator-open';
const INSPECTOR_STORAGE_KEY = 'tertius.timus.inspector-open';
const NAVIGATOR_WIDTH_STORAGE_KEY = 'tertius.timus.navigator-width';
const INSPECTOR_WIDTH_STORAGE_KEY = 'tertius.timus.inspector-width';

const clamp = (value: number, minimum: number, maximum: number): number =>
  Math.min(maximum, Math.max(minimum, value));

const readStoredBoolean = (key: string, fallback: boolean): boolean => {
  if (typeof window === 'undefined') return fallback;
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value === 'true';
  } catch {
    return fallback;
  }
};

const readStoredNumber = (key: string, fallback: number, minimum: number, maximum: number): number => {
  if (typeof window === 'undefined') return fallback;
  try {
    const value = Number(window.localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? clamp(value, minimum, maximum) : fallback;
  } catch {
    return fallback;
  }
};

const storeWorkspacePreference = (key: string, value: string | number | boolean) => {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Workspace preferences are optional in private or storage-restricted sessions.
  }
};

const toDraftingLayout = (value: unknown): DraftingLayout =>
  value === 'top' || value === 'front' || value === 'side' || value === 'iso'
    ? value
    : 'combined';

const toSafeScale = (value: unknown): number => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 1.0;
  const clamped = Math.min(10, Math.max(0.001, parsed));
  const firstPreset = draftingScalePresets[0];
  if (!firstPreset) return clamped;
  return draftingScalePresets.reduce((closest, item) =>
    Math.abs(item.value - clamped) < Math.abs(closest.value - clamped) ? item : closest
  , firstPreset).value;
};

export const DraftingTab: React.FC<{ serverUrl: string, isActive?: boolean }> = (props) => {
  const { authMode, login } = useAuth();
  if (authMode === 'guest') {
    return (
      <GuestWorkflowNotice
        title="Log in to generate drawings"
        message="Timus drafting sheets are generated from authenticated project artifacts."
        onLogin={login}
      />
    );
  }
  return <AuthenticatedDraftingTab {...props} />;
};

const AuthenticatedDraftingTab: React.FC<{ serverUrl: string, isActive?: boolean }> = ({ serverUrl, isActive = true }) => {
  const { getAccessToken } = useAuth();
  const [activeProject, setActiveProject] = useState<string>('');
  
  // Customizer State
  const [title, setTitle] = useState('UNTITLED PART');
  const [stampText, setStampText] = useState(DEFAULT_ISSUE_STATUS);
  const [showRedline, setShowRedline] = useState(true);
  const [showHiddenLines, setShowHiddenLines] = useState(false);
  const [scale, setScale] = useState(1.0);
  const [sheetSize, setSheetSize] = useState('A4');
  const [selectedView, setSelectedView] = useState<DraftingLayout>('combined');
  
  const [debouncedScale, setDebouncedScale] = useState(1.0);
  const [debouncedTitle, setDebouncedTitle] = useState('UNTITLED PART');
  
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [buildStatus, setBuildStatus] = useState<string>('none');
  const [buildMessage, setBuildMessage] = useState<string>('');
  const [navigatorOpen, setNavigatorOpen] = useState(() => readStoredBoolean(NAVIGATOR_STORAGE_KEY, false));
  const [inspectorOpen, setInspectorOpen] = useState(() => readStoredBoolean(INSPECTOR_STORAGE_KEY, true));
  const [navigatorWidth, setNavigatorWidth] = useState(() =>
    readStoredNumber(NAVIGATOR_WIDTH_STORAGE_KEY, 232, 200, 360));
  const [inspectorWidth, setInspectorWidth] = useState(() =>
    readStoredNumber(INSPECTOR_WIDTH_STORAGE_KEY, 300, 260, 420));
  const [focusMode, setFocusMode] = useState(false);
  const [canvasZoom, setCanvasZoom] = useState(1);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const compactWorkspace = window.matchMedia('(max-width: 1279px)');
    const collapseOverlayPanels = (matches: boolean) => {
      if (!matches) return;
      setNavigatorOpen(false);
      setInspectorOpen(false);
    };
    const handleChange = (event: MediaQueryListEvent) => collapseOverlayPanels(event.matches);

    collapseOverlayPanels(compactWorkspace.matches);
    compactWorkspace.addEventListener('change', handleChange);
    return () => compactWorkspace.removeEventListener('change', handleChange);
  }, []);

  const visibleNavigator = navigatorOpen && !focusMode;
  const visibleInspector = inspectorOpen && !focusMode;

  const toggleNavigator = () => {
    if (focusMode) setFocusMode(false);
    setNavigatorOpen((value) => {
      const next = focusMode ? true : !value;
      storeWorkspacePreference(NAVIGATOR_STORAGE_KEY, next);
      return next;
    });
  };

  const toggleInspector = () => {
    if (focusMode) setFocusMode(false);
    setInspectorOpen((value) => {
      const next = focusMode ? true : !value;
      storeWorkspacePreference(INSPECTOR_STORAGE_KEY, next);
      return next;
    });
  };

  const closeNavigator = () => {
    setNavigatorOpen(false);
    storeWorkspacePreference(NAVIGATOR_STORAGE_KEY, false);
  };

  const closeInspector = () => {
    setInspectorOpen(false);
    storeWorkspacePreference(INSPECTOR_STORAGE_KEY, false);
  };

  const beginPanelResize = useCallback((
    panel: 'navigator' | 'inspector',
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    event.preventDefault();
    const startingX = event.clientX;
    const startingWidth = panel === 'navigator' ? navigatorWidth : inspectorWidth;
    let latestWidth = startingWidth;

    const move = (pointerEvent: PointerEvent) => {
      const delta = panel === 'navigator'
        ? pointerEvent.clientX - startingX
        : startingX - pointerEvent.clientX;
      const nextWidth = panel === 'navigator'
        ? clamp(startingWidth + delta, 200, 360)
        : clamp(startingWidth + delta, 260, 420);
      latestWidth = nextWidth;

      if (panel === 'navigator') setNavigatorWidth(nextWidth);
      else setInspectorWidth(nextWidth);
    };

    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      storeWorkspacePreference(
        panel === 'navigator' ? NAVIGATOR_WIDTH_STORAGE_KEY : INSPECTOR_WIDTH_STORAGE_KEY,
        latestWidth,
      );
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  }, [inspectorWidth, navigatorWidth]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedScale(scale), 300);
    return () => clearTimeout(timer);
  }, [scale]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedTitle(title), 500);
    return () => clearTimeout(timer);
  }, [title]);

  useEffect(() => {
    if (!isActive) return;
    let isMounted = true;
    const fetchActive = async () => {
      if (!shouldRunPollingRequest()) return;
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
    const interval = setInterval(fetchActive, getPollingDelay(ACTIVE_PROJECT_POLL_INTERVAL_MS));
    return () => {
        isMounted = false;
        clearInterval(interval);
    };
  }, [serverUrl, getAccessToken, isActive]);

  useEffect(() => {
    if (!activeProject) return;
    let isMounted = true;

    const loadSettings = async () => {
      setSettingsLoaded(false);
      setTitle(activeProject.toUpperCase());
      setStampText(DEFAULT_ISSUE_STATUS);
      setShowRedline(true);
      setShowHiddenLines(false);
      setScale(1.0);
      setSheetSize('A4');
      setSelectedView('combined');

      try {
        const res = await apiFetch(`${serverUrl}/projects/${activeProject}/settings`, getAccessToken);
        if (!res.ok || !isMounted) return;
        const parsed = await res.json();
        if (parsed.title) setTitle(parsed.title);
        if (parsed.stamp_text) setStampText(parsed.stamp_text);
        if (parsed.show_redline !== undefined) setShowRedline(parsed.show_redline);
        if (parsed.show_hidden_lines !== undefined) setShowHiddenLines(parsed.show_hidden_lines);
        if (parsed.scale !== undefined) setScale(toSafeScale(parsed.scale));
        if (parsed.sheet_size) setSheetSize(parsed.sheet_size);
        setSelectedView(toDraftingLayout(parsed.layout));
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
      sheet_size: sheetSize,
      layout: selectedView,
    };
    apiFetch(`${serverUrl}/projects/${activeProject}/settings`, getAccessToken, {
      method: 'PUT',
      body: JSON.stringify(settings),
    }).catch(() => {
      console.error("Failed to save Timus settings");
    });
  }, [activeProject, settingsLoaded, title, stampText, showRedline, showHiddenLines, scale, sheetSize, selectedView, serverUrl, getAccessToken]);

  useEffect(() => {
    if (!activeProject || !isActive) return;
    let mounted = true;
    const checkStatus = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const res = await apiFetch(`${serverUrl}/projects/${activeProject}/drafting/status`, getAccessToken);
        if (res.ok && mounted) {
          const data = await res.json();
          setBuildStatus(data.status);
          setBuildMessage(data.user_message || '');
        }
      } catch (e) {
        setBuildMessage('Unable to check PDF Data status.');
      }
    };
    checkStatus();
    const interval = setInterval(checkStatus, getPollingDelay(MODEL_STATUS_POLL_INTERVAL_MS));
    return () => { mounted = false; clearInterval(interval); };
  }, [activeProject, isActive, serverUrl, getAccessToken]);

  const triggerBuild = async () => {
    if (!activeProject) return;
    setBuildStatus('building');
    setBuildMessage('');
    try {
      const res = await apiFetch(`${serverUrl}/projects/${activeProject}/drafting/build`, getAccessToken, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) {
        setBuildStatus(data.status === 'queued' ? 'building' : data.status || 'failed');
        setBuildMessage(data.user_message || data.error || 'PDF Data could not be started. Try again.');
        return;
      }
      setBuildStatus(data.status === 'queued' ? 'building' : data.status || 'building');
      setBuildMessage(data.user_message || '');
    } catch (e) {
      console.error(e);
      setBuildStatus('failed');
      setBuildMessage('PDF Data could not be started. Try again.');
    }
  };

  const handleDownloadPdf = async () => {
    if (!activeProject || buildStatus !== 'ready') return;
    await runWithInteractionSpan('artifact_download', {
      workflow: 'timus',
      artifact_type: 'drafting_pdf',
      sheet_size: sheetSize,
      layout: selectedView,
    }, async () => {
      try {
        const url = buildDraftingPdfUrl(serverUrl, activeProject, {
          title: debouncedTitle,
          issueStatus: stampText,
          showIssueMarkup: showRedline,
          showHiddenLines,
          scale: debouncedScale,
          sheetSize,
          layout: selectedView,
        });
        const res = await apiFetch(url, getAccessToken);
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          setBuildStatus('stale');
          setBuildMessage(data.user_message || 'Generate PDF Data before downloading the drafting PDF.');
          return;
        }
        const blob = await res.blob();
        const objectUrl = URL.createObjectURL(blob);
        window.open(objectUrl, '_blank', 'noopener,noreferrer');
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      } catch (e) {
        console.error("Failed to download PDF:", e);
      }
    });
  };

  if (!activeProject) {
    return (
      <div className="flex-1 flex justify-center items-center bg-slate-900 text-slate-500 font-mono text-sm">
        No active project found. Compile a project in Intus first.
      </div>
    );
  }

  return (
    <div
      data-testid="drafting-workspace"
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-slate-900 selection:bg-cyan-500/30"
    >
      <DraftingToolbar
        activeProject={activeProject}
        buildStatus={buildStatus}
        navigatorOpen={visibleNavigator}
        inspectorOpen={visibleInspector}
        focusMode={focusMode}
        onToggleNavigator={toggleNavigator}
        onToggleInspector={toggleInspector}
        onToggleFocusMode={() => setFocusMode((value) => !value)}
        onGenerate={triggerBuild}
        onDownload={handleDownloadPdf}
      />

      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        {visibleNavigator && (
          <>
            <DrawingSetNavigator
              width={navigatorWidth}
              selectedView={selectedView}
              onSelectView={setSelectedView}
              onClose={closeNavigator}
            />
            <div
              role="separator"
              aria-label="Resize drawing navigator"
              aria-orientation="vertical"
              onPointerDown={(event) => beginPanelResize('navigator', event)}
              className="hidden w-1 flex-none cursor-col-resize border-r border-slate-800 bg-slate-950 hover:bg-orange-500/40 xl:block"
            />
          </>
        )}

        <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-slate-800">
          <div className="flex min-h-9 flex-none items-center justify-between gap-3 border-b border-slate-700/70 bg-slate-900/80 px-3">
            <div className="min-w-0 truncate text-[10px] text-slate-400">
              WebGL preview <span className="text-slate-600">/</span> export PDF for authoritative vectors
            </div>
            <div className="flex flex-none items-center gap-1" aria-label="Sheet zoom controls">
              <button
                type="button"
                aria-label="Zoom out"
                disabled={canvasZoom <= 0.5}
                onClick={() => setCanvasZoom((value) => clamp(value - 0.25, 0.5, 2))}
                className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:border-slate-500 disabled:opacity-30"
              >
                -
              </button>
              <button
                type="button"
                onClick={() => setCanvasZoom(1)}
                className="min-w-14 rounded border border-slate-700 px-2 py-0.5 text-[10px] text-slate-300 hover:border-slate-500"
              >
                Fit {Math.round(canvasZoom * 100)}%
              </button>
              <button
                type="button"
                aria-label="Zoom in"
                disabled={canvasZoom >= 2}
                onClick={() => setCanvasZoom((value) => clamp(value + 0.25, 0.5, 2))}
                className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:border-slate-500 disabled:opacity-30"
              >
                +
              </button>
            </div>
          </div>

          <div data-testid="drafting-sheet-viewport" className="min-h-0 flex-1 overflow-auto p-2 sm:p-3">
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
              zoom={canvasZoom}
            />
          </div>
        </main>

        {visibleInspector && (
          <>
            <div
              role="separator"
              aria-label="Resize properties inspector"
              aria-orientation="vertical"
              onPointerDown={(event) => beginPanelResize('inspector', event)}
              className="hidden w-1 flex-none cursor-col-resize border-l border-slate-800 bg-slate-950 hover:bg-orange-500/40 xl:block"
            />
            <DraftingInspector
              width={inspectorWidth}
              sheetSize={sheetSize}
              title={title}
              issueStatus={stampText}
              selectedView={selectedView}
              scale={scale}
              showIssueMarkup={showRedline}
              showHiddenLines={showHiddenLines}
              onSheetSizeChange={setSheetSize}
              onTitleChange={setTitle}
              onIssueStatusChange={setStampText}
              onSelectedViewChange={(value) => setSelectedView(toDraftingLayout(value))}
              onScaleChange={setScale}
              onShowIssueMarkupChange={setShowRedline}
              onShowHiddenLinesChange={setShowHiddenLines}
              onClose={closeInspector}
            />
          </>
        )}
      </div>

      <DraftingStatusBar
        buildStatus={buildStatus}
        buildMessage={buildMessage}
        issueStatus={stampText}
        sheetSize={sheetSize}
        selectedView={selectedView}
        scaleLabel={formatDraftingScale(scale)}
      />
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
  selectedView: DraftingLayout;
  zoom: number;
}> = ({ sheetSize, title, stampText, showRedline, scale, serverUrl, activeProject, getAccessToken, isActive, selectedView, zoom }) => {
  const formats: Record<string, [number, number]> = {
    "A4": [297, 210], "A3": [420, 297], "A2": [594, 420], "A1": [841, 594], "A0": [1189, 841]
  };
  const [w, h] = formats[sheetSize] || [297, 210];
  const viewLayout = getDraftingViewLayout(selectedView, w, h);

  const svgRef = useRef<SVGSVGElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const accessTokenRef = useRef(getAccessToken);
  const modelMtimeRef = useRef<number>(0);
  const [fittedSheetSize, setFittedSheetSize] = useState({ width: 0, height: 0 });
  const [modelUrl, setModelUrl] = useState<string>('');

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const resize = () => {
      const bounds = host.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      const sheetRatio = w / h;
      const hostRatio = bounds.width / bounds.height;
      const width = hostRatio > sheetRatio ? bounds.height * sheetRatio : bounds.width;
      const height = width / sheetRatio;
      setFittedSheetSize({ width, height });
    };

    resize();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', resize);
      return () => window.removeEventListener('resize', resize);
    }

    const observer = new ResizeObserver(resize);
    observer.observe(host);
    return () => observer.disconnect();
  }, [h, w]);

  useEffect(() => {
    accessTokenRef.current = getAccessToken;
  }, [getAccessToken]);

  useEffect(() => {
    modelMtimeRef.current = 0;
    setModelUrl('');
  }, [activeProject]);
  
  // Model Polling
  useEffect(() => {
    if (!activeProject || !isActive) return;
    let mounted = true;
    const checkModel = async () => {
      if (!shouldRunPollingRequest()) return;
      try {
        const res = await apiFetch(`${serverUrl}/projects/${activeProject}/model_status`, accessTokenRef.current);
        if (res.ok && mounted) {
          const data = await res.json();
          if (data.mtime && data.mtime !== modelMtimeRef.current) {
            modelMtimeRef.current = data.mtime;
            setModelUrl(`${serverUrl}/projects/${activeProject}/model?t=${data.mtime}`);
          }
        }
      } catch (e) {}
    };
    checkModel();
    const interval = setInterval(checkModel, getPollingDelay(MODEL_STATUS_POLL_INTERVAL_MS));
    return () => { mounted = false; clearInterval(interval); };
  }, [serverUrl, activeProject, isActive]);

  const stateRef = useRef({ w, h, scale, selectedView });
  useEffect(() => {
    stateRef.current = { w, h, scale, selectedView };
  }, [w, h, scale, selectedView]);

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
    let modelBounds: THREE.Box3 | null = null;
    const loader = new GLTFLoader();
    
    apiFetch(modelUrl, accessTokenRef.current)
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
          modelBounds = new THREE.Box3().setFromObject(model);

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
    
    const fitOrthographicCameraToModel = (camera: THREE.OrthographicCamera, viewportWidthMm: number, viewportHeightMm: number, fitScale: number) => {
      if (!modelBounds) return;
      const bounds = modelBounds;
      if (bounds.min.equals(bounds.max)) return;

      const target = new THREE.Vector3();
      bounds.getCenter(target);
      camera.lookAt(target);
      camera.updateMatrixWorld();

      const safeScale = Math.max(1e-6, fitScale);
      const spanX = Math.max(1e-6, bounds.max.x - bounds.min.x);
      const spanY = Math.max(1e-6, bounds.max.y - bounds.min.y);
      const spanZ = Math.max(1e-6, bounds.max.z - bounds.min.z);
      const maxSpan = Math.max(spanX, spanY, spanZ);
      const unitsPerMm = maxSpan > 1000 ? 1 : 1000;

      const halfWidth = Math.max((viewportWidthMm / (2 * safeScale)) / unitsPerMm, 0.01);
      const halfHeight = Math.max((viewportHeightMm / (2 * safeScale)) / unitsPerMm, 0.01);

      camera.left = -halfWidth;
      camera.right = halfWidth;
      camera.top = halfHeight;
      camera.bottom = -halfHeight;

      const cameraDistance = camera.position.distanceTo(target);
      const depthSpan = Math.max(1e-6, spanZ);
      camera.near = Math.max(0.01, cameraDistance - depthSpan * 5);
      camera.far = cameraDistance + depthSpan * 5;
      camera.updateProjectionMatrix();
    };

    const updateCameras = (wMm: number, hMm: number) => {
      const s = stateRef.current;
      [topCam, frontCam, sideCam, isoCam].forEach((cam) => {
        if (cam instanceof THREE.OrthographicCamera) {
          fitOrthographicCameraToModel(cam, wMm, hMm, s.scale);
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
          updateCameras(wMm, hMm);
          renderer.render(scene, cam);
      };
      
      const cameras: Record<DraftingViewName, THREE.Camera> = {
        top: topCam,
        front: frontCam,
        side: sideCam,
        iso: isoCam,
      };
      const layout = getDraftingViewLayout(s.selectedView, s.w, s.h);
      (Object.entries(layout) as [DraftingViewName, DraftingViewRect][]).forEach(([viewName, rect]) => {
        renderView(cameras[viewName], rect.x, rect.y, rect.width, rect.height);
      });
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
    
    let resizeTimeout: ReturnType<typeof setTimeout> | undefined;
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
  }, [modelUrl]);

  return (
    <div ref={hostRef} className="flex h-full w-full">
      <div
        className="relative m-auto flex-none"
        style={{
          width: fittedSheetSize.width * zoom,
          height: fittedSheetSize.height * zoom,
          visibility: fittedSheetSize.width > 0 ? 'visible' : 'hidden',
        }}
      >
      <svg ref={svgRef} viewBox={`0 0 ${w} ${h}`} className="relative z-10 h-full w-full border border-slate-700 bg-white drop-shadow-2xl">
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
          <text x="52" y="7" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">DRAFT-001</text>
          
          <text x="82" y="3" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">SHEET NO.</text>
          <text x="82" y="7" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">1 OF 1</text>

          {/* Row 2 */}
          <text x="2" y="11" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">ISSUE STATUS</text>
          <text x="2" y="15" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">{stampText}</text>
          
          <text x="52" y="11" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">REVISION STATUS</text>
          <text x="52" y="15" fontSize="3.5" fontFamily="sans-serif" fill="#0f172a">P01</text>
          
          <text x="82" y="11" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">SCALE</text>
          <text x="82" y="15" fontSize="3.5" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">{formatDraftingScale(scale)}</text>

          {/* Row 3 */}
          <text x="2" y="19" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">DOCUMENT STATUS</text>
          <text x="2" y="23" fontSize="3.1" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">NOT FOR CONSTRUCTION</text>
          
          <text x="37" y="19" fontSize="2" fontFamily="monospace" fontWeight="bold" fill="#0f172a">SYSTEM</text>
          <text x="37" y="23" fontSize="3" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">TERTIUS DRAFTING WORKBENCH</text>

          {/* Stamp */}
          {showRedline && stampText && (
            <g>
              <rect x="1" y="9.5" width="48" height="7.5" fill="none" stroke="#ef4444" strokeWidth="0.3" />
            </g>
          )}
        </g>
        
        {/* Company Name */}
        <text x="15" y="25" fontSize="8" fontFamily="sans-serif" fontWeight="bold" fill="#0f172a">TERTIUS ENGINEERING</text>

        {/* View Grid Lines */}
        {/* View Labels */}
        <g fontSize="3" fontFamily="sans-serif" fontWeight="bold" fill="#9ca3af">
          {(Object.entries(viewLayout) as [DraftingViewName, DraftingViewRect][]).map(([viewName, rect]) => (
            <text key={viewName} x={rect.x} y={rect.y + rect.height - 2}>
              {{ top: 'TOP VIEW', front: 'FRONT ELEVATION', side: 'SIDE ELEVATION', iso: 'ISOMETRIC VIEW' }[viewName]}
            </text>
          ))}
        </g>
      </svg>
      
      <canvas 
        ref={canvasRef} 
        style={{
          position: 'absolute',
          inset: 0,
          pointerEvents: 'none',
          zIndex: 20
        }} 
      />
      </div>
    </div>
  );
};
