export type DraftingLayout = 'combined' | 'top' | 'front' | 'side' | 'iso';
export type DraftingViewName = Exclude<DraftingLayout, 'combined'>;
export type DraftingViewRect = { x: number; y: number; width: number; height: number };

export const formatDraftingScale = (scale: number): string =>
  scale >= 1 ? `${scale.toString()}:1` : `1:${(1 / scale).toString()}`;

export const getDraftingViewLayout = (
  layout: DraftingLayout,
  sheetWidth: number,
  sheetHeight: number,
): Partial<Record<DraftingViewName, DraftingViewRect>> => {
  if (layout !== 'combined') {
    return {
      [layout]: { x: 20, y: 30, width: sheetWidth - 40, height: sheetHeight - 75 },
    };
  }

  const width = (sheetWidth - 60) / 2;
  const height = (sheetHeight - 60) / 2;
  return {
    top: { x: 20, y: 30, width, height },
    iso: { x: 40 + width, y: 30, width, height },
    front: { x: 20, y: 30 + height, width, height },
    side: { x: 40 + width, y: 30 + height, width, height },
  };
};

export const buildDraftingPdfUrl = (
  serverUrl: string,
  activeProject: string,
  options: {
    title: string;
    issueStatus: string;
    showIssueMarkup: boolean;
    showHiddenLines: boolean;
    scale: number;
    sheetSize: string;
    layout: DraftingLayout;
  },
): string => {
  const params = new URLSearchParams({
    title: options.title,
    stamp: options.issueStatus,
    redline: options.showIssueMarkup.toString(),
    hidden_lines: options.showHiddenLines.toString(),
    scale: options.scale.toString(),
    size: options.sheetSize,
    layout: options.layout,
  });
  return `${serverUrl}/projects/${encodeURIComponent(activeProject)}/drafting.pdf?${params.toString()}`;
};


/** A part drawing declared in design.py (tertius.drawing), as listed by Timus. */
export interface PartDrawingSummary {
  name: string;
  title: string | null;
  number: string | null;
  sheet: string | null;
  views: string[];
  dimensions: number;
  error: string | null;
}

export const buildPartDrawingsUrl = (serverUrl: string, activeProject: string): string =>
  `${serverUrl}/projects/${encodeURIComponent(activeProject)}/drawings`;

export const buildPartDrawingPdfUrl = (serverUrl: string, activeProject: string, drawing: string): string =>
  `${buildPartDrawingsUrl(serverUrl, activeProject)}/${encodeURIComponent(drawing)}.pdf`;

const textOrNull = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() !== '' ? value : null;

/** The drawings list from GET /drawings, tolerating missing or partial data. */
export const parsePartDrawings = (data: unknown): PartDrawingSummary[] => {
  const items = (data as { drawings?: unknown } | null)?.drawings;
  if (!Array.isArray(items)) return [];
  return items.flatMap((item): PartDrawingSummary[] => {
    if (typeof item !== 'object' || item === null) return [];
    const record = item as Record<string, unknown>;
    const name = textOrNull(record.name);
    if (!name) return [];
    return [{
      name,
      title: textOrNull(record.title),
      number: textOrNull(record.number),
      sheet: textOrNull(record.sheet),
      views: Array.isArray(record.views) ? record.views.filter((v): v is string => typeof v === 'string') : [],
      dimensions: typeof record.dimensions === 'number' ? record.dimensions : 0,
      error: textOrNull(record.error),
    }];
  });
};
