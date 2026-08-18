import { describe, expect, it } from 'vitest';

import {
  buildDraftingPdfUrl,
  formatDraftingScale,
  getDraftingViewLayout,
} from './DraftingTab';

describe('Timus drafting layout contract', () => {
  it('includes the selected layout in the PDF request', () => {
    const relativeUrl = buildDraftingPdfUrl('/api/timus', 'shed project', {
      title: 'SHED & STORE',
      issueStatus: 'PRELIMINARY',
      showIssueMarkup: true,
      showHiddenLines: false,
      scale: 0.02,
      sheetSize: 'A3',
      layout: 'top',
    });
    const url = new URL(relativeUrl, 'https://example.test');

    expect(url.pathname).toBe('/api/timus/projects/shed%20project/drafting.pdf');
    expect(url.searchParams.get('layout')).toBe('top');
    expect(url.searchParams.get('title')).toBe('SHED & STORE');
    expect(url.searchParams.get('stamp')).toBe('PRELIMINARY');
    expect(url.searchParams.get('scale')).toBe('0.02');
    expect(url.searchParams.get('size')).toBe('A3');
  });

  it('gives a single view the usable sheet area', () => {
    expect(getDraftingViewLayout('side', 420, 297)).toEqual({
      side: { x: 20, y: 30, width: 380, height: 222 },
    });
  });

  it('keeps the established four-view combined layout', () => {
    const layout = getDraftingViewLayout('combined', 420, 297);

    expect(Object.keys(layout)).toEqual(['top', 'iso', 'front', 'side']);
    expect(layout.top).toEqual({ x: 20, y: 30, width: 180, height: 118.5 });
    expect(layout.side).toEqual({ x: 220, y: 148.5, width: 180, height: 118.5 });
  });

  it.each([
    [2, '2:1'],
    [1, '1:1'],
    [0.2, '1:5'],
    [0.01, '1:100'],
  ])('formats scale %s for the title block', (scale, label) => {
    expect(formatDraftingScale(scale)).toBe(label);
  });
});
