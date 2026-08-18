import type { CSSProperties } from 'react';

import type { DraftingLayout } from './draftingLayout';

export type { DraftingLayout } from './draftingLayout';

export type DraftingBuildStatus = 'none' | 'building' | 'ready' | 'stale' | 'failed' | string;

const controlClass =
  'w-full rounded border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-orange-400';

const layoutLabels: Record<DraftingLayout, string> = {
  combined: 'Combined sheet',
  top: 'Top view',
  front: 'Front elevation',
  side: 'Side elevation',
  iso: 'Isometric view',
};

const buildStatusLabels: Record<string, string> = {
  none: 'Not generated',
  building: 'Generating PDF data',
  ready: 'PDF data ready',
  stale: 'PDF data out of date',
  failed: 'PDF generation failed',
};

const buildStatusClasses: Record<string, string> = {
  building: 'border-orange-400/40 bg-orange-400/10 text-orange-200',
  ready: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200',
  stale: 'border-amber-400/40 bg-amber-400/10 text-amber-200',
  failed: 'border-red-400/40 bg-red-400/10 text-red-200',
};

const buildStatusTextClasses: Record<string, string> = {
  building: 'text-orange-300',
  ready: 'text-emerald-300',
  stale: 'text-amber-300',
  failed: 'text-red-300',
};

export const draftingScalePresets = [
  { value: 10, label: '10:1 (Enlarged 10x)' },
  { value: 5, label: '5:1 (Enlarged 5x)' },
  { value: 2, label: '2:1 (Enlarged 2x)' },
  { value: 1, label: '1:1 (Full Size)' },
  { value: 0.5, label: '1:2 (Half Size)' },
  { value: 0.25, label: '1:4' },
  { value: 0.2, label: '1:5' },
  { value: 0.1, label: '1:10' },
  { value: 0.05, label: '1:20' },
  { value: 0.02, label: '1:50' },
  { value: 0.01, label: '1:100' },
  { value: 0.005, label: '1:200' },
  { value: 0.002, label: '1:500' },
  { value: 0.001, label: '1:1000' },
];

const PanelHeading = ({ title, onClose, closeLabel }: {
  title: string;
  onClose: () => void;
  closeLabel: string;
}) => (
  <div className="flex h-12 flex-none items-center justify-between border-b border-slate-800 px-4">
    <h2 className="text-xs font-bold uppercase tracking-[0.18em] text-slate-300">{title}</h2>
    <button
      type="button"
      aria-label={closeLabel}
      onClick={onClose}
      className="rounded border border-slate-800 px-2 py-1 text-xs text-slate-400 hover:border-slate-600 hover:text-white"
    >
      Close
    </button>
  </div>
);

export function DraftingToolbar({
  activeProject,
  buildStatus,
  navigatorOpen,
  inspectorOpen,
  focusMode,
  onToggleNavigator,
  onToggleInspector,
  onToggleFocusMode,
  onGenerate,
  onDownload,
}: {
  activeProject: string;
  buildStatus: DraftingBuildStatus;
  navigatorOpen: boolean;
  inspectorOpen: boolean;
  focusMode: boolean;
  onToggleNavigator: () => void;
  onToggleInspector: () => void;
  onToggleFocusMode: () => void;
  onGenerate: () => void;
  onDownload: () => void;
}) {
  const statusClass = buildStatusClasses[buildStatus] ?? 'border-slate-700 bg-slate-800 text-slate-300';

  return (
    <header className="flex min-h-12 flex-none items-center gap-2 border-b border-slate-800 bg-slate-950 px-2 sm:px-3">
      <button
        type="button"
        aria-label={navigatorOpen ? 'Hide drawing navigator' : 'Show drawing navigator'}
        aria-pressed={navigatorOpen}
        onClick={onToggleNavigator}
        className={`rounded border px-2.5 py-1.5 text-xs font-semibold ${
          navigatorOpen
            ? 'border-orange-400/60 bg-orange-400/10 text-orange-200'
            : 'border-slate-700 text-slate-300 hover:border-slate-500'
        }`}
      >
        Drawings
      </button>

      <div className="min-w-0 flex-1 px-1">
        <div className="truncate text-xs font-semibold text-slate-200">
          Timus <span className="text-slate-600">/</span> {activeProject}{' '}
          <span className="text-slate-600">/</span> DRAFT-001
        </div>
        <div className="hidden truncate text-[10px] text-slate-500 sm:block">Interactive drawing-sheet workspace</div>
      </div>

      <span className={`hidden rounded border px-2 py-1 text-[10px] font-bold uppercase tracking-wide md:inline ${statusClass}`}>
        {buildStatusLabels[buildStatus] ?? buildStatus}
      </span>

      <button
        type="button"
        onClick={onGenerate}
        disabled={buildStatus === 'building'}
        className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs font-semibold text-slate-200 hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {buildStatus === 'building' ? 'Generating...' : 'Generate'}
      </button>
      <button
        type="button"
        onClick={onDownload}
        disabled={buildStatus !== 'ready'}
        className="rounded bg-orange-600 px-2.5 py-1.5 text-xs font-bold text-white hover:bg-orange-500 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
      >
        Export PDF
      </button>
      <button
        type="button"
        aria-label={focusMode ? 'Exit canvas focus mode' : 'Enter canvas focus mode'}
        aria-pressed={focusMode}
        onClick={onToggleFocusMode}
        className={`hidden rounded border px-2.5 py-1.5 text-xs lg:block ${
          focusMode ? 'border-cyan-400/60 text-cyan-200' : 'border-slate-700 text-slate-300 hover:border-slate-500'
        }`}
      >
        {focusMode ? 'Exit focus' : 'Focus'}
      </button>
      <button
        type="button"
        aria-label={inspectorOpen ? 'Hide properties inspector' : 'Show properties inspector'}
        aria-pressed={inspectorOpen}
        onClick={onToggleInspector}
        className={`rounded border px-2.5 py-1.5 text-xs font-semibold ${
          inspectorOpen
            ? 'border-orange-400/60 bg-orange-400/10 text-orange-200'
            : 'border-slate-700 text-slate-300 hover:border-slate-500'
        }`}
      >
        Properties
      </button>
    </header>
  );
}

export function DrawingSetNavigator({
  width,
  selectedView,
  onSelectView,
  onClose,
}: {
  width: number;
  selectedView: DraftingLayout;
  onSelectView: (layout: DraftingLayout) => void;
  onClose: () => void;
}) {
  const views: DraftingLayout[] = ['combined', 'top', 'front', 'side', 'iso'];

  return (
    <aside
      aria-label="Drawing navigator"
      style={{ '--navigator-width': `${width}px` } as CSSProperties}
      className="absolute inset-y-0 left-0 z-30 flex w-[var(--navigator-width)] flex-none flex-col border-r border-slate-800 bg-slate-950 shadow-2xl xl:relative xl:shadow-none"
    >
      <PanelHeading title="Drawing set" onClose={onClose} closeLabel="Hide drawing navigator" />
      <div className="flex-1 overflow-y-auto p-3">
        <div className="mb-2 flex items-center justify-between px-2">
          <span className="text-[10px] font-bold uppercase tracking-widest text-slate-500">Current sheet</span>
          <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[9px] uppercase text-slate-500">1 sheet</span>
        </div>
        <div className="mb-4 rounded border border-slate-800 bg-slate-900/60 p-3">
          <div className="text-xs font-bold text-slate-100">DRAFT-001</div>
          <div className="mt-1 truncate text-[11px] text-slate-400">Current project drawing</div>
          <div className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-orange-300">Preliminary</div>
        </div>

        <div className="mb-2 px-2 text-[10px] font-bold uppercase tracking-widest text-slate-500">Sheet views</div>
        <div className="space-y-1">
          {views.map((view) => (
            <button
              key={view}
              type="button"
              aria-current={selectedView === view ? 'page' : undefined}
              onClick={() => onSelectView(view)}
              className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-xs transition-colors ${
                selectedView === view
                  ? 'bg-orange-500/15 font-semibold text-orange-200'
                  : 'text-slate-400 hover:bg-slate-900 hover:text-slate-100'
              }`}
            >
              <span>{layoutLabels[view]}</span>
              {selectedView === view && <span className="text-[9px] uppercase tracking-wider">Shown</span>}
            </button>
          ))}
        </div>

        <div className="mt-5 rounded border border-amber-500/25 bg-amber-500/5 p-3 text-[10px] leading-relaxed text-amber-100/70">
          Top view is a projected view, not an architectural floor plan. True cut-plane views arrive in a later epic slice.
        </div>
      </div>
    </aside>
  );
}

export function DraftingInspector({
  width,
  sheetSize,
  title,
  issueStatus,
  selectedView,
  scale,
  showIssueMarkup,
  showHiddenLines,
  onSheetSizeChange,
  onTitleChange,
  onIssueStatusChange,
  onSelectedViewChange,
  onScaleChange,
  onShowIssueMarkupChange,
  onShowHiddenLinesChange,
  onClose,
}: {
  width: number;
  sheetSize: string;
  title: string;
  issueStatus: string;
  selectedView: DraftingLayout;
  scale: number;
  showIssueMarkup: boolean;
  showHiddenLines: boolean;
  onSheetSizeChange: (value: string) => void;
  onTitleChange: (value: string) => void;
  onIssueStatusChange: (value: string) => void;
  onSelectedViewChange: (value: DraftingLayout) => void;
  onScaleChange: (value: number) => void;
  onShowIssueMarkupChange: (value: boolean) => void;
  onShowHiddenLinesChange: (value: boolean) => void;
  onClose: () => void;
}) {
  return (
    <aside
      aria-label="Drawing properties"
      style={{ '--inspector-width': `${width}px` } as CSSProperties}
      className="absolute inset-y-0 right-0 z-30 flex w-[var(--inspector-width)] flex-none flex-col border-l border-slate-800 bg-slate-950 shadow-2xl xl:relative xl:shadow-none"
    >
      <PanelHeading title="Properties" onClose={onClose} closeLabel="Hide properties inspector" />
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        <details open className="group rounded border border-slate-800 bg-slate-900/40">
          <summary className="cursor-pointer select-none px-3 py-2 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400 group-open:text-slate-200">
            Sheet
          </summary>
          <div className="space-y-3 border-t border-slate-800 p-3">
            <label className="block space-y-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              Drawing title
              <input
                type="text"
                value={title}
                onChange={(event) => onTitleChange(event.target.value)}
                className={controlClass}
              />
            </label>
            <label className="block space-y-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              Sheet size
              <select value={sheetSize} onChange={(event) => onSheetSizeChange(event.target.value)} className={controlClass}>
                <option value="A4">A4 (297 x 210 mm)</option>
                <option value="A3">A3 (420 x 297 mm)</option>
                <option value="A2">A2 (594 x 420 mm)</option>
                <option value="A1">A1 (841 x 594 mm)</option>
                <option value="A0">A0 (1189 x 841 mm)</option>
              </select>
            </label>
            <label className="block space-y-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              Issue status
              <input
                type="text"
                value={issueStatus}
                onChange={(event) => onIssueStatusChange(event.target.value.substring(0, 32).toUpperCase())}
                placeholder="PRELIMINARY"
                className={controlClass}
              />
            </label>
          </div>
        </details>

        <details open className="group rounded border border-slate-800 bg-slate-900/40">
          <summary className="cursor-pointer select-none px-3 py-2 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400 group-open:text-slate-200">
            Viewport
          </summary>
          <div className="space-y-3 border-t border-slate-800 p-3">
            <label className="block space-y-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              Layout
              <select
                value={selectedView}
                onChange={(event) => onSelectedViewChange(event.target.value as DraftingLayout)}
                className={controlClass}
              >
                <option value="combined">Combined (4 views)</option>
                <option value="top">Top view only</option>
                <option value="front">Front elevation only</option>
                <option value="side">Side elevation only</option>
                <option value="iso">Isometric view only</option>
              </select>
            </label>
            <label className="block space-y-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              Projection scale
              <select value={scale.toString()} onChange={(event) => onScaleChange(Number(event.target.value))} className={controlClass}>
                {draftingScalePresets.map((entry) => (
                  <option value={entry.value.toString()} key={entry.value}>{entry.label}</option>
                ))}
              </select>
            </label>
          </div>
        </details>

        <details className="group rounded border border-slate-800 bg-slate-900/40">
          <summary className="cursor-pointer select-none px-3 py-2 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400 group-open:text-slate-200">
            Appearance
          </summary>
          <div className="space-y-3 border-t border-slate-800 p-3">
            <ToggleRow label="Highlight issue status" checked={showIssueMarkup} onChange={onShowIssueMarkupChange} />
            <ToggleRow label="Show hidden lines" checked={showHiddenLines} onChange={onShowHiddenLinesChange} />
          </div>
        </details>
      </div>
    </aside>
  );
}

function ToggleRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 text-xs text-slate-300">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-orange-500"
      />
    </label>
  );
}

export function DraftingStatusBar({
  buildStatus,
  buildMessage,
  issueStatus,
  sheetSize,
  selectedView,
  scaleLabel,
}: {
  buildStatus: DraftingBuildStatus;
  buildMessage: string;
  issueStatus: string;
  sheetSize: string;
  selectedView: DraftingLayout;
  scaleLabel: string;
}) {
  const statusClass = buildStatusTextClasses[buildStatus] ?? 'text-slate-400';

  return (
    <footer className="flex min-h-7 flex-none items-center gap-3 border-t border-slate-800 bg-slate-950 px-3 text-[10px] text-slate-500">
      <span className={`font-bold uppercase tracking-wide ${statusClass}`}>
        {buildStatusLabels[buildStatus] ?? buildStatus}
      </span>
      {buildMessage && <span className="min-w-0 flex-1 truncate">{buildMessage}</span>}
      {!buildMessage && <span className="flex-1" />}
      <span className="hidden sm:inline">{issueStatus || 'NO STATUS'}</span>
      <span>{layoutLabels[selectedView]}</span>
      <span>{scaleLabel}</span>
      <span>{sheetSize}</span>
    </footer>
  );
}
