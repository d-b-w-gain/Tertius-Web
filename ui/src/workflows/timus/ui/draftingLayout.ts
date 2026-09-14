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

