import { useMemo } from 'react'
import { LatestModelViewer } from '../workflows/extus/ui/ViewerTab'
import { resolveWorkflowServerUrl } from '../workflows/shared/apiConfig'
import { SHARE_TOKEN_HEADER } from './shareToken'

/** The read-only Extus viewer a share link opens (#417): no sign-in, no
 * workbench navigation, nothing that writes. */
export function SharedProjectPage({ token }: { token: string | null }) {
  const serverUrl = `${resolveWorkflowServerUrl('extus', import.meta.env?.VITE_API_URL)}/shared`
  const requestHeaders = useMemo(() => (token ? { [SHARE_TOKEN_HEADER]: token } : undefined), [token])

  return (
    <div className="flex h-screen w-screen flex-col bg-slate-950 text-slate-100 font-sans">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-2">
        <span className="text-lg font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">
          Tertius
        </span>
        <span className="text-xs text-slate-400">Shared read-only view</span>
      </header>
      <main className="relative flex min-h-0 flex-1 flex-col">
        {requestHeaders ? (
          <LatestModelViewer serverUrl={serverUrl} requestHeaders={requestHeaders} />
        ) : (
          <div className="m-auto max-w-sm text-center text-sm text-slate-400">
            This share link is incomplete. Ask whoever sent it for the full link.
          </div>
        )}
      </main>
    </div>
  )
}
