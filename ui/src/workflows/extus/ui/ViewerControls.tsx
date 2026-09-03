import type { StructuralViewerOverlay } from '../model/viewer';

const stageFocusStatusStyle: Record<
  NonNullable<StructuralViewerOverlay['stageFocus']>['status'],
  string
> = {
  pass: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-200',
  fail: 'border-red-500/60 bg-red-500/15 text-red-200',
  warning: 'border-amber-500/50 bg-amber-500/10 text-amber-200',
  not_checked: 'border-slate-600 bg-slate-800/70 text-slate-300',
  unsupported: 'border-fuchsia-500/50 bg-fuchsia-500/10 text-fuchsia-200',
  blocked: 'border-red-500/50 bg-red-950/50 text-red-200',
};

const stageLegendToneStyle: Record<
  NonNullable<StructuralViewerOverlay['stageFocus']>['legend'][number]['tone'],
  string
> = {
  verified: 'bg-emerald-400',
  candidate: 'bg-amber-400',
  missing: 'bg-red-400',
  demand: 'bg-cyan-400',
  neutral: 'bg-slate-400',
};

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
  const stageFocus = structuralOverlays?.find((overlay) => overlay.stageFocus)?.stageFocus;

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
        {stageFocus ? (
          <div
            className={`rounded border px-2 py-0.5 text-xs font-bold ${stageFocusStatusStyle[stageFocus.status]}`}
            title={stageFocus.summary}
          >
            Stage {stageFocus.order} focus - {stageFocus.status.replace('_', ' ')}
          </div>
        ) : structuralOverlays?.length ? (
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
      {stageFocus && (
        <div
          className={`max-w-4xl rounded border px-3 py-2 ${stageFocusStatusStyle[stageFocus.status]}`}
          data-testid="structural-stage-focus"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[10px] font-bold uppercase tracking-[0.16em] opacity-75">
                Stage {stageFocus.order} visual check
              </div>
              <div className="mt-0.5 text-sm font-semibold text-slate-100">
                {stageFocus.label}
              </div>
              <p className="mt-1 text-[10px] leading-relaxed text-slate-300">
                {stageFocus.visualDescription}
              </p>
            </div>
            {stageFocus.combinationLabel && (
              <span className="rounded border border-current/30 bg-slate-950/50 px-2 py-1 font-mono text-[9px]">
                {stageFocus.combinationLabel}
              </span>
            )}
          </div>
          <p className="mt-2 text-[10px] leading-relaxed opacity-85">
            {stageFocus.summary}
          </p>
          {stageFocus.metrics.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {stageFocus.metrics.map((metric) => (
                <span
                  key={`${metric.label}-${metric.value}`}
                  className="rounded border border-current/20 bg-slate-950/45 px-2 py-1 text-[9px]"
                >
                  <span className="opacity-65">{metric.label}</span>{' '}
                  <span className="font-mono font-semibold text-slate-100">{metric.value}</span>
                </span>
              ))}
            </div>
          )}
          {stageFocus.legend.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-slate-300">
              {stageFocus.legend.map((item) => (
                <span key={item.label} className="flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-full ${stageLegendToneStyle[item.tone]}`} />
                  {item.label}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="text-xs text-slate-400" aria-live="polite">
        {loadErrorText || (isModelLoading ? 'Loading model...' : statusText)}
      </div>
    </div>
  );
}
