import { useState } from 'react'

import { apiFetch } from '../../api/client'
import type { SiteDefinition, WindStandardEvidence } from './contracts'


const DIRECTIONS = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'] as const

type StandardTableEvidencePanelProps = {
  serverUrl: string
  getAccessToken: () => Promise<string>
  site: SiteDefinition
  evidence: WindStandardEvidence | null
  onApply: (evidence: WindStandardEvidence) => void
}

export function StandardTableEvidencePanel({
  serverUrl,
  getAccessToken,
  site,
  evidence,
  onApply,
}: StandardTableEvidencePanelProps) {
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState<'pdf' | 'json' | null>(null)
  const currentEvidence = evidence?.region === site.wind.region ? evidence : null

  const download = async (kind: 'pdf' | 'json') => {
    setDownloadError(null)
    setDownloading(kind)
    try {
      const path = kind === 'pdf' ? '/report/site-wind.pdf' : '/report/evidence'
      const response = await apiFetch(`${serverUrl}${path}`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(site),
      })
      if (!response.ok) throw new Error(`Site report returned ${response.status}`)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = kind === 'pdf'
        ? 'tertius-site-wind-basis.pdf'
        : 'tertius-site-wind-evidence.json'
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : 'Could not download site report')
    } finally {
      setDownloading(null)
    }
  }

  return (
    <section className="rounded border border-slate-800 bg-slate-900/50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-slate-100">Digitised standard-table evidence</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">
            Table 3.2(A) supplies the eight Australian direction multipliers and Table 3.3
            supplies Mc. Applying these values keeps them editable and does not mark the
            project tables verified.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void download('pdf')} disabled={downloading !== null}
            className="rounded border border-cyan-500/60 bg-cyan-950/30 px-3 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-950/60 disabled:opacity-50">
            {downloading === 'pdf' ? 'Building report…' : 'Download site wind report'}
          </button>
          <button type="button" onClick={() => void download('json')} disabled={downloading !== null}
            className="rounded border border-slate-600 px-3 py-2 text-xs font-semibold text-slate-200 hover:border-cyan-500 disabled:opacity-50">
            {downloading === 'json' ? 'Building evidence…' : 'Evidence JSON'}
          </button>
        </div>
      </div>

      {!currentEvidence ? (
        <div className="mt-3 rounded border border-amber-500/30 bg-amber-950/20 p-3 text-xs text-amber-200">
          Recalculate to load table evidence for wind region {site.wind.region}.
        </div>
      ) : (
        <>
          <div className="mt-3 overflow-x-auto rounded border border-slate-800">
            <table className="w-full min-w-[620px] border-collapse text-xs">
              <thead className="bg-slate-950/80 text-slate-400">
                <tr>
                  <th className="px-3 py-2 text-left">Region</th>
                  {DIRECTIONS.map((direction) => (
                    <th key={direction} className="px-3 py-2 text-right uppercase">{direction}</th>
                  ))}
                  <th className="px-3 py-2 text-right">Mc</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-slate-800 font-mono text-cyan-200">
                  <td className="px-3 py-2 font-sans font-semibold text-slate-200">{currentEvidence.region}</td>
                  {DIRECTIONS.map((direction) => (
                    <td key={direction} className="px-3 py-2 text-right">
                      {currentEvidence.direction_multipliers[direction].toFixed(2)}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-right">
                    {currentEvidence.climate_change_multiplier.toFixed(2)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-amber-300">
              Secondary summary · licensed standard and amendments must still be checked.
            </p>
            <button type="button" onClick={() => onApply(currentEvidence)}
              className="rounded border border-cyan-500/60 bg-cyan-950/30 px-3 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-950/60">
              Use Table 3.2(A) Md and Table 3.3 Mc
            </button>
          </div>
          <details className="mt-3 rounded border border-slate-800 bg-slate-950/50 p-3 text-xs">
            <summary className="cursor-pointer font-semibold text-slate-300">
              Report table index ({currentEvidence.report_table_index.length} tables)
            </summary>
            <ul className="mt-2 grid gap-1 text-slate-500 md:grid-cols-2">
              {currentEvidence.report_table_index.map((table) => (
                <li key={table.id}>
                  Table {table.table_number} · {table.title} · source p.{table.source_page}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
      {downloadError && <p className="mt-2 text-xs text-red-300">{downloadError}</p>}
    </section>
  )
}
