import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DraftingTab } from './DraftingTab';

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
}));
const originalMatchMedia = window.matchMedia;

vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }));
vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'authenticated', getAccessToken: mocks.getAccessToken }),
}));

const jsonResponse = (data: unknown, ok = true) => ({
  ok,
  json: vi.fn().mockResolvedValue(data),
});

describe('Timus canvas-first workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mocks.apiFetch.mockImplementation((url: string, _token: unknown, init?: RequestInit) => {
      if (url.endsWith('/project_name')) return Promise.resolve(jsonResponse({ project_name: 'shed' }));
      if (url.endsWith('/settings') && init?.method === 'PUT') return Promise.resolve(jsonResponse({ success: true }));
      if (url.endsWith('/settings')) {
        return Promise.resolve(jsonResponse({
          title: 'SHED',
          stamp_text: 'PRELIMINARY',
          show_redline: true,
          show_hidden_lines: false,
          scale: 0.02,
          sheet_size: 'A3',
          layout: 'front',
        }));
      }
      if (url.endsWith('/drafting/status')) return Promise.resolve(jsonResponse({ status: 'ready' }));
      if (url.endsWith('/model_status')) return Promise.resolve(jsonResponse({ mtime: null }));
      return Promise.resolve(jsonResponse({}));
    });
  });

  afterEach(() => {
    cleanup();
    if (originalMatchMedia) window.matchMedia = originalMatchMedia;
    else Reflect.deleteProperty(window, 'matchMedia');
  });

  it('keeps the sheet central while panels can be independently opened and focused', async () => {
    render(<DraftingTab serverUrl="/api/timus" isActive />);

    await screen.findByTestId('drafting-workspace');
    expect(screen.getByTestId('drafting-sheet-viewport')).toBeInTheDocument();
    expect(screen.getByLabelText('Drawing properties')).toBeInTheDocument();
    expect(screen.queryByLabelText('Drawing navigator')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show drawing navigator' }));
    expect(screen.getByLabelText('Drawing navigator')).toBeInTheDocument();
    expect(window.localStorage.getItem('tertius.timus.navigator-open')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Enter canvas focus mode' }));
    expect(screen.queryByLabelText('Drawing navigator')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Drawing properties')).not.toBeInTheDocument();
    expect(screen.getByTestId('drafting-sheet-viewport')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Exit canvas focus mode' }));
    expect(screen.getByLabelText('Drawing navigator')).toBeInTheDocument();
    expect(screen.getByLabelText('Drawing properties')).toBeInTheDocument();

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/timus/projects/shed/settings',
        mocks.getAccessToken,
        expect.objectContaining({ method: 'PUT' }),
      );
    });
  });

  it('provides fit and bounded zoom controls without changing drawing scale', async () => {
    render(<DraftingTab serverUrl="/api/timus" isActive />);
    await screen.findByTestId('drafting-workspace');

    expect(screen.getByRole('button', { name: 'Fit 100%' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(screen.getByRole('button', { name: 'Fit 125%' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Fit 125%' }));
    expect(screen.getByRole('button', { name: 'Fit 100%' })).toBeInTheDocument();
  });

  it('starts with overlay panels collapsed at compact widths', async () => {
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true,
      media: '(max-width: 1279px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    });

    render(<DraftingTab serverUrl="/api/timus" isActive />);
    await screen.findByTestId('drafting-workspace');

    expect(screen.queryByLabelText('Drawing navigator')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Drawing properties')).not.toBeInTheDocument();
    expect(screen.getByTestId('drafting-sheet-viewport')).toBeInTheDocument();
  });

  it('lists part drawings from a ready build and opens one as a PDF', async () => {
    const pdfBlob = new Blob(['%PDF-1.4'], { type: 'application/pdf' });
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const createObjectURL = vi.fn(() => 'blob:part-drawing');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const base = mocks.apiFetch.getMockImplementation();
    mocks.apiFetch.mockImplementation((url: string, token: unknown, init?: RequestInit) => {
      if (url.endsWith('/projects/shed/drawings')) {
        return Promise.resolve(jsonResponse({
          drawings: [
            { name: 'Knee_Plate', title: 'Portal knee gusset plate', number: '3X5-PL-KNEE', sheet: 'A3',
              views: ['FRONT', 'RIGHT', 'ISOMETRIC'], dimensions: 6, error: null },
            { name: 'Broken', title: 'Broken', number: null, sheet: 'A3', views: [], dimensions: 0,
              error: 'DrawingError: circle is not seen face-on' },
          ],
        }));
      }
      if (url.endsWith('/projects/shed/drawings/Knee_Plate.pdf')) {
        return Promise.resolve({ ok: true, status: 200, blob: vi.fn().mockResolvedValue(pdfBlob) });
      }
      return base!(url, token, init);
    });

    render(<DraftingTab serverUrl="/api/timus" isActive />);
    await screen.findByTestId('drafting-workspace');
    fireEvent.click(screen.getByRole('button', { name: 'Show drawing navigator' }));

    const knee = await screen.findByRole('button', { name: 'Open part drawing Knee_Plate' });
    expect(knee).toHaveTextContent('3X5-PL-KNEE');
    expect(knee).toHaveTextContent('Portal knee gusset plate');
    const broken = screen.getByRole('button', { name: 'Open part drawing Broken' });
    expect(broken).toBeDisabled();
    expect(broken).toHaveTextContent('circle is not seen face-on');

    fireEvent.click(knee);
    await waitFor(() => expect(open).toHaveBeenCalledWith('blob:part-drawing', '_blank', 'noopener,noreferrer'));
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/timus/projects/shed/drawings/Knee_Plate.pdf', mocks.getAccessToken);
    expect(createObjectURL).toHaveBeenCalledWith(pdfBlob);
    open.mockRestore();
  });

  it('shows no part drawings section when the build has none', async () => {
    render(<DraftingTab serverUrl="/api/timus" isActive />);
    await screen.findByTestId('drafting-workspace');
    fireEvent.click(screen.getByRole('button', { name: 'Show drawing navigator' }));
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalledWith('/api/timus/projects/shed/drawings', mocks.getAccessToken));
    expect(screen.queryByLabelText('Part drawings')).not.toBeInTheDocument();
  });
});
