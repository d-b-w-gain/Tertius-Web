type GuestImportBannerProps = {
  importError: string | null
  isImporting: boolean
  onDismiss: () => void
  onImport: () => void
}

export function GuestImportBanner({ importError, isImporting, onDismiss, onImport }: GuestImportBannerProps) {
  return (
    <div className="flex items-center gap-3 border-b border-cyan-900/50 bg-cyan-950/40 px-4 py-2 text-sm text-cyan-100">
      <span className="min-w-0 flex-1">
        Import your local guest draft into this account.
        {importError && <span className="ml-2 text-red-300">{importError}</span>}
      </span>
      <button type="button" onClick={onImport} disabled={isImporting} className="rounded bg-cyan-600 px-3 py-1 font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-60">
        {isImporting ? 'Importing...' : 'Import'}
      </button>
      <button type="button" onClick={onDismiss} className="rounded px-2 py-1 text-cyan-200 hover:bg-cyan-900/60">
        Dismiss
      </button>
    </div>
  )
}
