import type { StructuralViewerOverlay } from './ViewerTab';

export interface ViewerControlsProps {
  projectName?: string;
  structuralOverlays?: StructuralViewerOverlay[];
  renderQuality: 'high' | 'low';
  showGrid: boolean;
  autoRotate: boolean;
  loadErrorText: string | null;
  isModelLoading: boolean;
  statusText: string;
  onFit: () => void;
  onToggleRenderQuality: () => void;
  onToggleGrid: () => void;
  onToggleAutoRotate: () => void;
}

export function ViewerControls({
  projectName,
  structuralOverlays,
  renderQuality,
  showGrid,
  autoRotate,
  loadErrorText,
  isModelLoading,
  statusText,
  onFit,
  onToggleRenderQuality,
  onToggleGrid,
  onToggleAutoRotate,
}: ViewerControlsProps) {
  return (
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
        {structuralOverlays?.length ? (
          <div
            className="text-xs font-bold text-amber-200 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/40"
            title={structuralOverlays.map((overlay) => overlay.label).join('\n')}
          >
            {structuralOverlays.length} analytical {
              structuralOverlays[0]?.mode === 'displacement' ? 'deflection' : 'moment'
            } ribbon{structuralOverlays.length === 1 ? '' : 's'} ·{' '}
            {structuralOverlays.reduce(
              (count, overlay) => count + (overlay.loadArrows?.length ?? 0),
              0,
            )} loads · {structuralOverlays.reduce(
              (count, overlay) => count + (overlay.nodes?.length ?? 0),
              0,
            )} nodes · {structuralOverlays.reduce(
              (count, overlay) => count + (overlay.reactions?.length ?? 0),
              0,
            )} reactions · {structuralOverlays.reduce(
              (count, overlay) => count + (overlay.restraintSegments?.length ?? 0),
              0,
            )} restraint traces
          </div>
        ) : null}
        <button
          onClick={onFit}
          className="pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border border-slate-700 bg-slate-800 text-slate-300 transition-colors hover:border-sky-500 hover:text-sky-300"
          title="Frame the whole model"
          aria-label="Frame the whole model"
        >
          Fit
        </button>
        <button
          onClick={onToggleRenderQuality}
          className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${renderQuality === 'high' ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
        >
          Visuals: {renderQuality === 'high' ? 'High' : 'Low'}
        </button>
        <button
          onClick={onToggleGrid}
          className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${showGrid ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
        >
          Grid: {showGrid ? 'ON' : 'OFF'}
        </button>
        <button
          onClick={onToggleAutoRotate}
          className={`pointer-events-auto text-xs font-bold px-2 py-0.5 rounded border transition-colors ${autoRotate ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
        >
          Rotate: {autoRotate ? 'ON' : 'OFF'}
        </button>
      </div>
      <div className="text-xs text-slate-400" aria-live="polite">
        {loadErrorText || (isModelLoading ? 'Loading model...' : statusText)}
      </div>
    </div>
  );
}
