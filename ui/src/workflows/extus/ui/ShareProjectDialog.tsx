import React, { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../../api/client';
import { shareLinkUrl } from '../../../share/shareToken';

/** A read-only link to the active project, as the server lists it (#417). */
export interface ProjectShareLink {
  id: string;
  token: string;
  workbench: string;
  created_at: string;
}

interface ShareProjectDialogProps {
  serverUrl: string;
  getAccessToken: () => Promise<string>;
  onClose: () => void;
}

async function readError(response: Response, fallback: string) {
  try {
    const body = await response.json();
    return typeof body?.error === 'string' ? body.error : fallback;
  } catch {
    return fallback;
  }
}

export const ShareProjectDialog: React.FC<ShareProjectDialogProps> = ({ serverUrl, getAccessToken, onClose }) => {
  const [projectName, setProjectName] = useState('');
  const [shares, setShares] = useState<ProjectShareLink[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await apiFetch(`${serverUrl}/shares`, getAccessToken);
      if (!response.ok) throw new Error(await readError(response, 'Share links could not be loaded.'));
      const body = await response.json();
      setProjectName(body.project_name || '');
      setShares(Array.isArray(body.shares) ? body.shares : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Share links could not be loaded.');
    } finally {
      setIsLoading(false);
    }
  }, [serverUrl, getAccessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const createLink = async () => {
    setIsBusy(true);
    setError(null);
    try {
      const response = await apiFetch(`${serverUrl}/shares`, getAccessToken, { method: 'POST' });
      if (!response.ok) throw new Error(await readError(response, 'The link could not be created.'));
      const share: ProjectShareLink = await response.json();
      setShares((current) => [share, ...current]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The link could not be created.');
    } finally {
      setIsBusy(false);
    }
  };

  const revokeLink = async (share: ProjectShareLink) => {
    setIsBusy(true);
    setError(null);
    try {
      const response = await apiFetch(`${serverUrl}/shares/${share.id}`, getAccessToken, { method: 'DELETE' });
      if (!response.ok && response.status !== 404) {
        throw new Error(await readError(response, 'The link could not be revoked.'));
      }
      setShares((current) => current.filter((item) => item.id !== share.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The link could not be revoked.');
    } finally {
      setIsBusy(false);
    }
  };

  const copyLink = async (share: ProjectShareLink) => {
    try {
      await navigator.clipboard.writeText(shareLinkUrl(share.token));
      setCopiedId(share.id);
      window.setTimeout(() => setCopiedId((current) => (current === share.id ? null : current)), 2000);
    } catch {
      setError('Copying failed; select the link and copy it instead.');
    }
  };

  return (
    <div
      className="absolute inset-0 z-30 flex items-center justify-center bg-slate-950/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="share-project-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-slate-700 bg-slate-900 p-5 text-slate-200 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="share-project-title" className="text-base font-semibold text-white">
              Share {projectName || 'project'}
            </h2>
            <p className="mt-1 text-xs text-slate-400">
              Anyone with a link can view this project's latest model in a read-only viewer, without signing in.
              Revoke a link to stop it working.
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white" aria-label="Close">
            ✕
          </button>
        </div>

        {error && <p className="mt-3 rounded border border-red-500/40 bg-red-950/40 px-3 py-2 text-xs text-red-200">{error}</p>}

        <div className="mt-4 space-y-2">
          {isLoading ? (
            <p className="text-xs text-slate-400">Loading links…</p>
          ) : shares.length === 0 ? (
            <p className="text-xs text-slate-400">No links yet.</p>
          ) : (
            shares.map((share) => (
              <div key={share.id} className="rounded border border-slate-700 bg-slate-950/60 p-2">
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    value={shareLinkUrl(share.token)}
                    onFocus={(event) => event.currentTarget.select()}
                    className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-300"
                    aria-label="Share link"
                  />
                  <button
                    onClick={() => void copyLink(share)}
                    className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-200 hover:border-sky-500 hover:text-sky-300"
                  >
                    {copiedId === share.id ? 'Copied' : 'Copy'}
                  </button>
                  <button
                    onClick={() => void revokeLink(share)}
                    disabled={isBusy}
                    className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-200 hover:border-red-500 hover:text-red-300 disabled:opacity-40"
                  >
                    Revoke
                  </button>
                </div>
                <p className="mt-1 text-[11px] text-slate-500">Created {new Date(share.created_at).toLocaleString()}</p>
              </div>
            ))
          )}
        </div>

        <div className="mt-4 flex justify-end">
          <button
            onClick={() => void createLink()}
            disabled={isBusy || isLoading}
            className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            Create link
          </button>
        </div>
      </div>
    </div>
  );
};
